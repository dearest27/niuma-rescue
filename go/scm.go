package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type pubResult struct {
	OK     bool
	Note   string
	Link   string
	Detail string
}

func git(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	if dir != "" {
		cmd.Dir = dir
	}
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// scmPrepare 为需求准备（或复用）独立 worktree + 分支。幂等。
func scmPrepare(ws Workspace, reqID string) (workPath, branch string, err error) {
	base := filepath.Join(cfg.WorktreeBase, ws.safeKey())
	if e := os.MkdirAll(base, 0o755); e != nil {
		return "", "", e
	}
	workPath = filepath.Join(base, "REQ-"+reqID)
	branch = "niuma/REQ-" + reqID
	if fi, e := os.Stat(filepath.Join(workPath, ".git")); e == nil || (fi != nil && fi.IsDir()) {
		return workPath, branch, nil // 复用
	}
	if _, e := os.Stat(workPath); e == nil {
		return workPath, branch, nil // 目录已在（worktree 元数据可能在别处）
	}
	// 尽力 fetch 一下 base
	git(ws.Path, "fetch", "--quiet", "origin")
	out, e := git(ws.Path, "worktree", "add", "-B", branch, workPath, ws.BaseRef)
	if e != nil {
		return "", "", errf("worktree 创建失败: %s", strings.TrimSpace(out))
	}
	return workPath, branch, nil
}

func changedFiles(ws Workspace, wt string) []string {
	out, _ := git(wt, "diff", "--name-only", ws.BaseRef+"...HEAD")
	var files []string
	for _, l := range strings.Split(out, "\n") {
		if l = strings.TrimSpace(l); l != "" {
			files = append(files, l)
		}
	}
	return files
}

func diffText(ws Workspace, wt string) string {
	out, _ := git(wt, "diff", ws.BaseRef+"...HEAD")
	return out
}

func gitCommitAll(wt, msg string) {
	git(wt, "add", "-A")
	git(wt, "commit", "-m", msg) // 没改动则失败，忽略
}

// afterDevelop：开发完成后发布（push 开则推分支）。
func afterDevelop(ws Workspace, wt, branch string) pubResult {
	if !ws.PushEnabled {
		return pubResult{OK: true, Note: "未开 push（本地分支 " + branch + "）", Link: branch}
	}
	out, err := git(wt, "push", "-u", "origin", branch, "--force-with-lease")
	if err != nil {
		return pubResult{OK: false, Detail: strings.TrimSpace(out)}
	}
	return pubResult{OK: true, Note: "已推送 " + branch, Link: branch}
}

// afterReview：review 通过后建 PR（github via gh）或仅 push。
func afterReview(ws Workspace, wt, branch, title, body string) pubResult {
	if ws.PushEnabled {
		if out, err := git(wt, "push", "-u", "origin", branch, "--force-with-lease"); err != nil {
			return pubResult{OK: false, Detail: strings.TrimSpace(out)}
		}
	}
	if ws.PREnabled && ws.PRProvider == "github" {
		args := []string{"pr", "create", "--title", title, "--body", body, "--head", branch, "--base", ws.TargetBranch}
		if ws.GHRepo != "" {
			args = append(args, "--repo", ws.GHRepo)
		}
		cmd := exec.Command("gh", args...)
		cmd.Dir = wt
		out, err := cmd.CombinedOutput()
		if err != nil {
			return pubResult{OK: false, Detail: strings.TrimSpace(string(out))}
		}
		link := strings.TrimSpace(lastNonEmpty(string(out)))
		return pubResult{OK: true, Note: "已建 PR", Link: link}
	}
	if ws.PushEnabled {
		return pubResult{OK: true, Note: "已推送 " + branch, Link: branch}
	}
	return pubResult{OK: true, Note: "未开 push/PR（本地分支 " + branch + "）", Link: branch}
}

func lastNonEmpty(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.TrimSpace(lines[i]) != "" {
			return strings.TrimSpace(lines[i])
		}
	}
	return ""
}
