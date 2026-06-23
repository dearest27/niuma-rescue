package main

import (
	"encoding/json"
	"os"
	"regexp"
	"strings"
)

type Workspace struct {
	Key          string
	Path         string
	SCM          string // git | svn
	BaseRef      string
	TargetBranch string
	TestCmd      string
	PRProvider   string // none | github | gitlab
	GHRepo       string
	PushEnabled  bool
	PREnabled    bool
}

type wsFile struct {
	Default string            `json:"default"`
	Items   map[string]wsItem `json:"items"`
}
type wsItem struct {
	Path         string `json:"path"`
	SCM          string `json:"scm"`
	Base         string `json:"base"`
	TargetBranch string `json:"target_branch"`
	TestCmd      string `json:"test_cmd"`
	PRProvider   string `json:"pr_provider"`
	GHRepo       string `json:"gh_repo"`
	PushEnabled  bool   `json:"push_enabled"`
	PREnabled    bool   `json:"pr_enabled"`
}

var wsCache *wsFile

func loadWorkspaces() *wsFile {
	if wsCache != nil {
		return wsCache
	}
	wsCache = &wsFile{Items: map[string]wsItem{}}
	b, err := os.ReadFile(cfg.WorkspacesFile)
	if err == nil {
		json.Unmarshal(b, wsCache)
	}
	return wsCache
}

// defaultWorkspace 从 .env 合成（没有 workspaces.json 时）。
func defaultWorkspace() Workspace {
	return Workspace{
		Key: "default", Path: cfg.RepoPath, SCM: "git", BaseRef: cfg.BaseRef,
		TargetBranch: lastSeg(cfg.BaseRef), TestCmd: cfg.TestCmd, PRProvider: "none",
		GHRepo: cfg.GHRepo, PushEnabled: cfg.PushEnabled, PREnabled: cfg.PREnabled,
	}
}

func workspaceGet(key string) (Workspace, error) {
	f := loadWorkspaces()
	if len(f.Items) == 0 {
		if key == "" || key == "default" {
			return defaultWorkspace(), nil
		}
		return Workspace{}, errf("未知工作区 `%s`：未配置 workspaces.json", key)
	}
	if key == "" {
		key = f.Default
	}
	it, ok := f.Items[key]
	if !ok {
		var keys []string
		for k := range f.Items {
			keys = append(keys, k)
		}
		return Workspace{}, errf("未知工作区 `%s`，可选：%s", key, strings.Join(keys, ", "))
	}
	scm := it.SCM
	if scm == "" {
		scm = "git"
	}
	base := it.Base
	if base == "" {
		base = "origin/main"
	}
	tb := it.TargetBranch
	if tb == "" {
		tb = lastSeg(base)
	}
	prov := it.PRProvider
	if prov == "" {
		prov = "none"
	}
	return Workspace{
		Key: key, Path: it.Path, SCM: scm, BaseRef: base, TargetBranch: tb,
		TestCmd: it.TestCmd, PRProvider: prov, GHRepo: it.GHRepo,
		PushEnabled: it.PushEnabled, PREnabled: it.PREnabled,
	}, nil
}

func workspaceKeys() []string {
	f := loadWorkspaces()
	if len(f.Items) == 0 {
		return []string{"default"}
	}
	var keys []string
	for k := range f.Items {
		keys = append(keys, k)
	}
	return keys
}

func (w Workspace) safeKey() string {
	return regexp.MustCompile(`[^A-Za-z0-9_.-]`).ReplaceAllString(w.Key, "_")
}

var wsTokenRe = regexp.MustCompile(`#([A-Za-z0-9_.-]+)`)

// parseWorkspaceToken 从文本里抽 #key（返回去掉 token 的文本 + key）。
func parseWorkspaceToken(body string) (string, string) {
	m := wsTokenRe.FindStringSubmatch(body)
	if m == nil {
		return body, ""
	}
	cleaned := strings.TrimSpace(wsTokenRe.ReplaceAllString(body, ""))
	return cleaned, m[1]
}

func lastSeg(ref string) string {
	if i := strings.LastIndexByte(ref, '/'); i >= 0 {
		return ref[i+1:]
	}
	return ref
}
