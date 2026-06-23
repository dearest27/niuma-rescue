package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type App struct {
	fs    *Feishu
	st    *Store
	sem   chan struct{} // 并发上限
	gitMu sync.Mutex    // 串行化共享 base 仓库上的 worktree 操作
}

var agentLog = func(s string) { logf("%s", s) }

// ── 状态推进 / 失败 ───────────────────────────────────────────────────
func (a *App) advance(rec *Record, status, logLine string, extra map[string]any) error {
	cur := fieldText(rec.Fields[FStatus])
	if status != cur && !ValidTransitions[cur][status] {
		return errf("非法状态流转：%s -> %s", cur, status)
	}
	prev := fieldText(rec.Fields[FLog])
	newLog := strings.TrimSpace(prev+logLine+"\n") + "\n"
	fields := map[string]any{FStatus: status, FLog: newLog}
	for k, v := range extra {
		fields[k] = v
	}
	if err := a.fs.updateRecord(rec.RecordID, fields); err != nil {
		return err
	}
	for k, v := range fields {
		rec.Fields[k] = v
	}
	emit("dispatcher", "transition", map[string]any{"record_id": rec.RecordID, "from": cur, "to": status})
	return nil
}

func (a *App) onFailure(rec *Record, msg, retryStatus string) {
	fails := fieldInt(rec.Fields[FFails]) + 1
	line := fmt.Sprintf("[fail#%d] %s", fails, msg)
	chat := fieldText(rec.Fields[FChat])
	switch {
	case fails >= cfg.FailureLimit:
		a.advance(rec, SBlocked, line+" → 已阻塞", map[string]any{FFails: fails})
		a.fs.notifyCard(chat, blockedCard(rec, msg))
		a.fs.notify(chat, "⚠️ 需求「"+fieldText(rec.Fields[FTitle])+"」已阻塞，需人工介入。原因："+trunc(msg, 200))
	case retryStatus != "" && retryStatus != fieldText(rec.Fields[FStatus]):
		a.advance(rec, retryStatus, line+" → 回到"+retryStatus, map[string]any{FFails: fails})
	default:
		prev := fieldText(rec.Fields[FLog])
		newLog := strings.TrimSpace(prev+line+"\n") + "\n"
		a.fs.updateRecord(rec.RecordID, map[string]any{FFails: fails, FLog: newLog})
		rec.Fields[FFails] = fails
		rec.Fields[FLog] = newLog
	}
}

func (a *App) retryDelay(fails int) int {
	if fails < 1 {
		fails = 1
	}
	d := cfg.RetryBase << (fails - 1)
	if d > 900 {
		d = 900
	}
	return d
}

func (a *App) resolveAgent(rec *Record, stage, def string) string {
	field := map[string]string{"clarify": FAgentClarify, "code": FAgentCode, "review": FAgentReview}[stage]
	raw := fieldText(rec.Fields[field])
	if raw == "" {
		raw = fieldText(rec.Fields[FAgent])
	}
	if raw == "" {
		return def
	}
	e := normalizeAgent(raw)
	if _, ok := AgentCmds[e]; !ok {
		return def
	}
	return e
}

func (a *App) workspaceFor(rec *Record) Workspace {
	ws, err := workspaceGet(fieldText(rec.Fields[FWorkspace]))
	if err != nil {
		ws, _ = workspaceGet("")
	}
	return ws
}

func (a *App) dossierDir(wt, reqID string) string {
	d := filepath.Join(wt, dossierDir, "REQ-"+reqID)
	os.MkdirAll(d, 0o755)
	return d
}

func (a *App) writeDossier(wt, reqID string, f map[string]any) {
	d := a.dossierDir(wt, reqID)
	os.WriteFile(filepath.Join(d, "prd.md"), []byte(fieldText(f[FPRD])), 0o644)
	os.WriteFile(filepath.Join(d, "requirement.md"), []byte(fieldText(f[FDesc])), 0o644)
}

// ── 进度回调（心跳 + 飞书卡片）────────────────────────────────────────
func (a *App) makeProgress(chatID, title, stage, engine, runID string) func(map[string]any) {
	wantCard := chatID != "" && cfg.ProgressInterval > 0
	var mid string
	var lastCard, lastHB float64
	start := time.Now()
	return func(stats map[string]any) {
		el := time.Since(start).Seconds()
		if runID != "" && el-lastHB >= 15 {
			lastHB = el
			a.st.heartbeat(runID)
		}
		if !wantCard {
			return
		}
		if mid != "" && el-lastCard < float64(cfg.ProgressInterval) {
			return
		}
		c := progressCard(title, stage, engine, stats)
		if mid == "" {
			if m, err := a.fs.sendCard(chatID, c); err == nil {
				mid = m
			}
		} else {
			a.fs.patchCard(mid, c)
		}
		lastCard = el
	}
}

// ── 阶段处理器 ────────────────────────────────────────────────────────
func (a *App) handleClarify(rec *Record, runID string) {
	f := rec.Fields
	ws := a.workspaceFor(rec)
	chat := fieldText(f[FChat])
	engine := a.resolveAgent(rec, "clarify", cfg.EngineClarify)
	prompt := buildPrompt("clarify", map[string]string{"requirement": fieldText(f[FDesc]), "clarifications": fieldText(f[FClarify])})
	a.fs.notify(chat, "🤔 正在澄清需求（"+engine+" · "+ws.Key+"）…")
	prog := a.makeProgress(chat, recTitle(rec), "澄清", engine, runID)
	res := runAgent(engine, prompt, ws.Path, cfg.TimeoutClarify, agentLog, prog)
	if !res.OK {
		a.onFailure(rec, "clarify("+engine+") 调用失败: "+trunc(res.Output, 300), "")
		return
	}
	verdict, rest := routeClarify(res.Output)
	if verdict == "CLEAR" {
		a.advance(rec, SConfirm, "[clarify:"+engine+"] 信息充分，PRD 已生成，待人确认", map[string]any{FPRD: rest})
		a.fs.notifyCard(chat, confirmCard(rec))
	} else {
		merged := strings.TrimSpace(fieldText(f[FClarify]) + "\n\n" + strings.TrimSpace(res.Output))
		a.advance(rec, SAnswer, "[clarify:"+engine+"] 产出澄清问题，待人回答", map[string]any{FClarify: merged})
		a.fs.notify(chat, "🤔 关于这个需求我有几个问题，直接回复我即可：\n\n"+strings.TrimSpace(res.Output))
	}
}

func (a *App) handleDevelop(rec *Record, runID string) {
	rid := rec.RecordID
	f := rec.Fields
	chat := fieldText(f[FChat])
	ws := a.workspaceFor(rec)
	a.gitMu.Lock()
	wt, branch, err := scmPrepare(ws, rid)
	a.gitMu.Unlock()
	if err != nil {
		a.onFailure(rec, "worktree: "+err.Error(), "")
		return
	}
	a.writeDossier(wt, rid, f)
	engine := a.resolveAgent(rec, "code", cfg.EngineCode)
	changed := changedFiles(ws, wt)
	if len(changed) == 0 {
		a.fs.notify(chat, "🔧 开始开发（"+engine+"）：写代码 + 跑测试，可能需要几分钟，请稍候…")
		prog := a.makeProgress(chat, recTitle(rec), "开发", engine, runID)
		prompt := buildPrompt("code", map[string]string{"req_id": rid, "dossier": a.dossierDir(wt, rid)})
		res := runAgent(engine, prompt, wt, cfg.TimeoutCode, agentLog, prog)
		if !res.OK {
			a.onFailure(rec, "coder("+engine+") 调用失败: "+trunc(res.Output, 300), "")
			return
		}
		a.gitMu.Lock()
		gitCommitAll(wt, "[niuma] REQ-"+rid)
		a.gitMu.Unlock()
		changed = changedFiles(ws, wt)
	}
	okTest, detail := a.runGate(ws, wt)
	emit("dispatcher", "gate_done", map[string]any{"record_id": rid, "ok": okTest})
	if !okTest {
		a.fs.notify(chat, "❌ 测试未通过，将重试（"+engine+"）。")
		a.onFailure(rec, "测试未通过: "+tail(detail, 400), "")
		return
	}
	pub := afterDevelop(ws, wt, branch)
	if !pub.OK {
		a.onFailure(rec, "发布失败: "+pub.Detail, "")
		return
	}
	a.advance(rec, SReview, "[code:"+engine+"] 完成、测试通过、"+pub.Note, map[string]any{FLink: pub.Link})
	a.fs.notify(chat, "✅ 开发完成（"+engine+"）：改动 "+itoa(len(changed))+" 个文件 · 测试通过 · "+pub.Note+"，进入 Review。")
}

func (a *App) handleReview(rec *Record, runID string) {
	rid := rec.RecordID
	f := rec.Fields
	chat := fieldText(f[FChat])
	ws := a.workspaceFor(rec)
	a.gitMu.Lock()
	wt, branch, err := scmPrepare(ws, rid)
	a.gitMu.Unlock()
	if err != nil {
		a.onFailure(rec, "worktree: "+err.Error(), "")
		return
	}
	engine := a.resolveAgent(rec, "review", cfg.EngineReview)
	prompt := buildPrompt("review", map[string]string{"dossier": a.dossierDir(wt, rid), "diff": diffText(ws, wt)})
	a.fs.notify(chat, "🔍 开始 Review（"+engine+"）：审查改动中…")
	prog := a.makeProgress(chat, recTitle(rec), "Review", engine, runID)
	res := runAgent(engine, prompt, wt, cfg.TimeoutReview, agentLog, prog)
	if !res.OK {
		a.onFailure(rec, "reviewer("+engine+") 调用失败: "+trunc(res.Output, 300), "")
		return
	}
	if reviewVerdict(res.Output) == "PASS" {
		title := fieldText(f[FTitle])
		if title == "" {
			title = branch
		}
		pub := afterReview(ws, wt, branch, title, fieldText(f[FPRD]))
		if !pub.OK {
			a.onFailure(rec, "发布失败: "+pub.Detail, "")
			return
		}
		link := pub.Link
		if link == "" {
			link = fieldText(f[FLink])
		}
		if link == "" {
			link = branch
		}
		a.advance(rec, SMerge, "[review:"+engine+"] PASS，"+pub.Note+"，待人工合并", map[string]any{FLink: link})
		a.fs.notify(chat, "✅ Review 通过（"+engine+"）！"+pub.Note+"。")
		a.fs.notifyCard(chat, mergeCard(rec))
	} else {
		a.fs.notify(chat, "🛠 Review 未通过，打回开发：\n"+trunc(res.Output, 500))
		a.onFailure(rec, "Review 未过:\n"+trunc(res.Output, 400), SDev)
	}
}

// ── 验收门（相对基线）─────────────────────────────────────────────────
func anyCodeFile(files []string) bool {
	for _, fpath := range files {
		for _, ext := range CodeExts {
			if strings.HasSuffix(fpath, ext) {
				return true
			}
		}
	}
	return false
}

func runTest(cmdStr, dir string) (int, string) {
	cmd := exec.Command("sh", "-c", cmdStr)
	cmd.Dir = dir
	out, _ := cmd.CombinedOutput()
	return cmd.ProcessState.ExitCode(), string(out)
}

func errorCount(out string) int {
	n := 0
	for _, l := range strings.Split(out, "\n") {
		if GateErrorRe.MatchString(l) {
			n++
		}
	}
	return n
}

func (a *App) runGate(ws Workspace, wt string) (bool, string) {
	if strings.TrimSpace(ws.TestCmd) == "" {
		return true, "(未设验收门)"
	}
	if !anyCodeFile(changedFiles(ws, wt)) {
		return true, "纯非代码改动，跳过验收门"
	}
	afterRC, afterOut := runTest(ws.TestCmd, wt)
	if afterRC == 0 {
		return true, "验收门通过（绿）"
	}
	if !cfg.GateRelative {
		return false, tail(afterOut, 400)
	}
	baseRC, baseOut, ok := a.baselineRun(ws)
	if !ok {
		return false, "验收失败且无法建立基线对比：\n" + tail(afterOut, 400)
	}
	if baseRC == 0 {
		return false, "基线本是通过的，本次改动引入了失败：\n" + tail(afterOut, 400)
	}
	afterN, baseN := errorCount(afterOut), errorCount(baseOut)
	if afterN <= baseN {
		return true, fmt.Sprintf("基线本就红（%d 处），本次未新增（%d 处）→ 相对基线放行", baseN, afterN)
	}
	return false, fmt.Sprintf("错误数 %d → %d，本次引入新问题：\n%s", baseN, afterN, tail(afterOut, 400))
}

func (a *App) baselineRun(ws Workspace) (int, string, bool) {
	tmp, err := os.MkdirTemp(cfg.WorktreeBase, "baseline-")
	if err != nil {
		return 0, "", false
	}
	a.gitMu.Lock()
	_, e := git(ws.Path, "worktree", "add", "--detach", tmp, ws.BaseRef)
	a.gitMu.Unlock()
	if e != nil {
		os.RemoveAll(tmp)
		return 0, "", false
	}
	defer func() {
		a.gitMu.Lock()
		git(ws.Path, "worktree", "remove", "--force", tmp)
		a.gitMu.Unlock()
		os.RemoveAll(tmp)
	}()
	if _, err := os.Stat(filepath.Join(ws.Path, "node_modules")); err == nil {
		os.Symlink(filepath.Join(ws.Path, "node_modules"), filepath.Join(tmp, "node_modules"))
	}
	rc, out := runTest(ws.TestCmd, tmp)
	return rc, out, true
}

// ── 链式处理一条记录 + tick ───────────────────────────────────────────
func (a *App) runStage(status string, rec *Record, runID string) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = errf("%v", r)
		}
	}()
	switch status {
	case SClarify:
		a.handleClarify(rec, runID)
	case SDev:
		a.handleDevelop(rec, runID)
	case SReview:
		a.handleReview(rec, runID)
	}
	return nil
}

func (a *App) processChain(rec *Record) {
	for Actionable[fieldText(rec.Fields[FStatus])] {
		status := fieldText(rec.Fields[FStatus])
		title := recTitle(rec)
		stage := map[string]string{SClarify: "clarify", SDev: "develop", SReview: "review"}[status]
		claim := a.st.claim(rec.RecordID, stage, status, title)
		if !claim.OK {
			if claim.Reason == "retry_wait" {
				logf("跳过「%s」· 状态=%s · 等待下次重试", title, status)
			} else {
				logf("跳过「%s」· 状态=%s · 已有执行锁(%s)", title, status, claim.Reason)
			}
			return
		}
		logf("处理「%s」· 状态=%s · run=%s", title, status, claim.RunID)
		err := a.runStage(status, rec, claim.RunID)
		newStatus := fieldText(rec.Fields[FStatus])
		if err != nil {
			logf("  → 异常：%v", err)
			a.onFailure(rec, status+" 阶段异常: "+err.Error(), "")
			a.st.fail(claim.RunID, status+" 阶段异常: "+err.Error(), a.retryDelay(fieldInt(rec.Fields[FFails])))
			return
		}
		logf("  → 完成，新状态=%s", newStatus)
		if newStatus == status {
			a.st.fail(claim.RunID, status+" 阶段未推进，等待重试", a.retryDelay(fieldInt(rec.Fields[FFails])))
			return
		}
		a.st.complete(claim.RunID, status, newStatus, "stage advanced")
	}
}

// tick：扫描一轮，对每条 actionable 记录在并发上限内 fire-and-forget 处理。
// 同一记录的重复处理由 store.claim 防护；不同记录用各自 worktree，安全并行。
func (a *App) tick() {
	records, err := a.fs.listRecords()
	if err != nil {
		elog("list records 失败: %v", err)
		return
	}
	var actionable []Record
	for _, r := range records {
		if Actionable[fieldText(r.Fields[FStatus])] {
			actionable = append(actionable, r)
		}
	}
	emit("dispatcher", "tick_scanned", map[string]any{"records": len(records), "actionable": len(actionable)})
	logf("扫描 %d 条记录，待处理 %d 条", len(records), len(actionable))
	for i := range actionable {
		rec := actionable[i]
		select {
		case a.sem <- struct{}{}:
			go func(r Record) {
				defer func() { <-a.sem }()
				a.processChain(&r)
			}(rec)
		default:
			// 并发已满，留给下次 trigger/兜底轮询
		}
	}
}
