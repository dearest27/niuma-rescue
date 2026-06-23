package main

import (
	"regexp"
	"strings"
)

var intakeRe = regexp.MustCompile(`^需求(?:\s*@([A-Za-z][\w -]*))?\s*[：:]?\s*([\s\S]*)$`)
var spaceRe = regexp.MustCompile(`\s+`)

var commandHelp = `可用指令：
看板（所有在途需求一览）
状态（当前会话的需求）
配置（点按选择 Agent / 工作区）
健康（服务在不在跑 / 有无卡住）
统计 / 周报（运行报表）
重试 / 清锁 / 解除阻塞 / 重新澄清 / 完成
切换Agent <claude|codex|gemini|cursor>
切换工作区 <workspace>
设置状态 <状态>

新需求格式：
需求：修改登录页按钮样式
  发完会先停在「待选择」，选好 Agent/工作区点「开始澄清」才开跑（PIPELINE_SETUP_GATE=0 可关）。`

var activeStatuses = map[string]bool{
	SSetup: true, SClarify: true, SAnswer: true, SConfirm: true,
	SDev: true, SReview: true, SMerge: true, SBlocked: true,
}

func parseIntake(text string) (body, agent, wsKey string, ok bool) {
	m := intakeRe.FindStringSubmatch(text)
	if m == nil {
		return "", "", "", false
	}
	body = strings.Trim(strings.TrimSpace(m[2]), "：: ")
	body, wsKey = parseWorkspaceToken(body)
	body = strings.Trim(strings.TrimSpace(body), "：: ")
	agent = normalizeAgent(m[1])
	return body, agent, wsKey, true
}

func findActive(records []Record, chatID string) *Record {
	var found *Record
	for i := range records {
		r := &records[i]
		if fieldText(r.Fields[FChat]) == chatID && activeStatuses[fieldText(r.Fields[FStatus])] {
			found = r // 取最后一条（最新）
		}
	}
	return found
}

func findRecordStatus(records []Record, chatID string, statuses map[string]bool) *Record {
	for i := range records {
		r := &records[i]
		if fieldText(r.Fields[FChat]) == chatID && statuses[fieldText(r.Fields[FStatus])] {
			return r
		}
	}
	return nil
}

func findByID(records []Record, rid string) *Record {
	for i := range records {
		if records[i].RecordID == rid {
			return &records[i]
		}
	}
	return nil
}

func (a *App) sendCardOrText(chatID string, c map[string]any, fallback string) {
	if _, err := a.fs.sendCard(chatID, c); err != nil {
		a.fs.sendText(chatID, fallback)
	}
}

func recordDiag(r *Record) string {
	f := r.Fields
	clar := orDefault(fieldText(f[FAgentClarify]), orDefault(fieldText(f[FAgent]), cfg.EngineClarify))
	code := orDefault(fieldText(f[FAgentCode]), orDefault(fieldText(f[FAgent]), cfg.EngineCode))
	rev := orDefault(fieldText(f[FAgentReview]), orDefault(fieldText(f[FAgent]), cfg.EngineReview))
	return strings.Join([]string{
		"需求诊断：" + recTitle(r),
		"· record_id: " + r.RecordID,
		"· 状态: " + orDefault(fieldText(f[FStatus]), "-"),
		"· 失败次数: " + fieldText(f[FFails]),
		"· 工作区: " + orDefault(fieldText(f[FWorkspace]), "默认"),
		"· Agent: 澄清=" + clar + " / 开发=" + code + " / Review=" + rev,
		"· 最近日志: " + orDefault(lastLog(r), "-"),
	}, "\n")
}

// handleCommand 返回 (matched, dispatch)。matched=false 表示不是命令，交给后续 intake。
func (a *App) handleCommand(text, chatID string, records []Record) (bool, bool) {
	n := strings.TrimSpace(spaceRe.ReplaceAllString(text, " "))
	switch n {
	case "指令", "帮助", "help", "/help":
		a.fs.sendText(chatID, commandHelp)
		return true, false
	case "看板", "全部", "board", "/board":
		a.sendCardOrText(chatID, boardCard(records), boardText(records))
		return true, false
	case "配置", "config", "/config":
		rec := findActive(records, chatID)
		if rec == nil {
			a.fs.sendText(chatID, "当前会话没有进行中的需求。")
			return true, false
		}
		a.sendCardOrText(chatID, settingsCard(rec, workspaceKeys()), "回复『切换Agent cursor』『切换工作区 <key>』来设置。")
		return true, false
	case "健康", "系统", "health":
		a.fs.sendText(chatID, systemHealthText(a.st))
		return true, false
	case "统计", "报表", "stats":
		a.fs.sendText(chatID, summaryText(24))
		return true, false
	case "周报", "weekly":
		a.fs.sendText(chatID, summaryText(168))
		return true, false
	case "诊断", "diagnose":
		rec := findActive(records, chatID)
		if rec == nil {
			a.fs.sendText(chatID, "当前会话没有进行中的需求。")
			return true, false
		}
		a.fs.sendText(chatID, recordDiag(rec))
		return true, false
	case "状态":
		rec := findActive(records, chatID)
		if rec == nil {
			a.fs.sendText(chatID, "当前会话没有进行中的需求。")
			return true, false
		}
		a.sendCardOrText(chatID, statusCard(rec), "状态："+fieldText(rec.Fields[FStatus]))
		return true, false
	case "开始澄清", "开始", "go":
		rec := findRecordStatus(records, chatID, map[string]bool{SSetup: true})
		if rec == nil {
			return false, false // 不在待选择语境 → 交给后续
		}
		a.fs.updateRecord(rec.RecordID, map[string]any{FStatus: SClarify})
		a.fs.sendText(chatID, "🚀 开始澄清。")
		return true, true
	}

	// 带参数命令
	rec := findActive(records, chatID)
	var res *opResult
	switch {
	case n == "重试":
		if rec == nil {
			break
		}
		r := a.retryRecord(rec)
		res = &r
	case n == "清锁":
		if rec == nil {
			break
		}
		r := a.clearLock(rec)
		res = &r
	case n == "重新澄清":
		if rec == nil {
			break
		}
		r := a.restartClarify(rec)
		res = &r
	case n == "完成" || n == "标记完成":
		if rec == nil {
			break
		}
		r := a.markDone(rec)
		res = &r
	case strings.HasPrefix(n, "解除阻塞"):
		if rec == nil {
			break
		}
		target := SDev
		if f := strings.Fields(n); len(f) == 2 {
			target = f[1]
		}
		r := a.unblockRecord(rec, target)
		res = &r
	case strings.HasPrefix(n, "切换工作区 ") || strings.HasPrefix(n, "设置工作区 "):
		if rec == nil {
			break
		}
		r := a.setWorkspace(rec, strings.TrimSpace(n[len("切换工作区 "):]))
		res = &r
	case agentCmdRe.MatchString(n):
		if rec == nil {
			break
		}
		m := agentCmdRe.FindStringSubmatch(n)
		stage := map[string]string{"澄清": "clarify", "开发": "code", "Review": "review", "review": "review"}[m[1]]
		r := a.setAgent(rec, m[2], stage)
		res = &r
	case statusCmdRe.MatchString(n):
		if rec == nil {
			break
		}
		m := statusCmdRe.FindStringSubmatch(n)
		r := a.setStatus(rec, m[1])
		res = &r
	default:
		return false, false // 不是命令
	}
	if rec == nil {
		a.fs.sendText(chatID, "当前会话没有可操作的需求。")
		return true, false
	}
	if res == nil {
		return false, false
	}
	prefix := "✅"
	if !res.ok {
		prefix = "⚠️"
	}
	a.fs.sendText(chatID, prefix+" "+res.msg)
	return true, res.dispatch
}

var agentCmdRe = regexp.MustCompile(`^(?:切换|设置)(澄清|开发|Review|review)?Agent (.+)$`)
var statusCmdRe = regexp.MustCompile(`^设置状态 (待选择|待澄清|待回答|待确认|开发中|Review中|待合并|完成|已阻塞)$`)

func (a *App) appendClarify(rec *Record, answer string) {
	merged := strings.TrimSpace(fieldText(rec.Fields[FClarify]) + "\n\n【回答】" + answer)
	a.fs.updateRecord(rec.RecordID, map[string]any{FClarify: merged, FStatus: SClarify})
	rec.Fields[FClarify] = merged
	rec.Fields[FStatus] = SClarify
}

// handleMessage 处理一条飞书文本消息。返回 true 表示进入"机器该处理"的状态，需触发 dispatch。
func (a *App) handleMessage(msg map[string]any) bool {
	if fieldText(msg["message_type"]) != "text" {
		return false
	}
	chatID := fieldText(msg["chat_id"])
	text := strings.TrimSpace(fieldText(msg["content"]))
	sender := fieldText(msg["sender_id"])
	if chatID == "" || text == "" {
		return false
	}
	records, err := a.fs.listRecords()
	if err != nil {
		elog("list records: %v", err)
		return false
	}
	if matched, dispatch := a.handleCommand(text, chatID, records); matched {
		return dispatch
	}
	// 人工卡点输入
	if rec := findRecordStatus(records, chatID, HumanInput); rec != nil {
		status := fieldText(rec.Fields[FStatus])
		if status == SAnswer {
			a.appendClarify(rec, text)
			a.fs.sendText(chatID, "👌 收到，我再看看还需不需要补充。")
			return true
		}
		if status == SConfirm {
			low := strings.ToLower(text)
			confirmed := false
			for _, w := range ConfirmWords {
				if strings.Contains(low, strings.ToLower(w)) {
					confirmed = true
					break
				}
			}
			if confirmed {
				a.fs.updateRecord(rec.RecordID, map[string]any{FStatus: SDev})
				a.fs.sendText(chatID, "🚀 已确认，开始开发，完成后我会同步进度。")
			} else {
				a.appendClarify(rec, text)
				a.fs.sendText(chatID, "已记录你的补充，我再过一遍。")
			}
			return true
		}
		return false
	}
	// 新需求
	body, agent, wsKey, ok := parseIntake(text)
	if !ok {
		a.fs.sendText(chatID, "发「需求：<一句话描述>」给我，就能提交一个新需求开始走流水线。")
		return false
	}
	// 幂等：同会话+同描述已有在途记录 → 跳过
	for _, r := range records {
		if fieldText(r.Fields[FChat]) == chatID && fieldText(r.Fields[FDesc]) == body {
			st := fieldText(r.Fields[FStatus])
			if st != SDone && st != SBlocked {
				return false
			}
		}
	}
	a.fs.sendText(chatID, "✅ 收到需求，正在准备…")
	gated := cfg.SetupGate
	status := SClarify
	if gated {
		status = SSetup
	}
	fields := map[string]any{FTitle: trunc(body, 30), FDesc: body, FStatus: status, FChat: chatID}
	if sender != "" {
		fields[FOwner] = []map[string]string{{"id": sender}}
	}
	if wsKey != "" {
		fields[FWorkspace] = wsKey
	}
	if agent != "" {
		fields[FAgent] = agent
	}
	created, err := a.fs.createRecord(fields)
	if err != nil {
		a.fs.sendText(chatID, "建记录失败："+err.Error())
		return false
	}
	if gated {
		a.sendCardOrText(chatID, settingsCard(created, workspaceKeys()),
			"需求已收到。回复『切换Agent <名>』『切换工作区 <key>』选好后回『开始澄清』。")
		return false
	}
	a.fs.sendText(chatID, "🔍 已收到，正在澄清需求…")
	return true
}

// handleCardAction 处理卡片按钮。返回 (toast, dispatch, 替换卡片或 nil)。
func (a *App) handleCardAction(value map[string]any) (string, bool, map[string]any) {
	rid := fieldText(value["record_id"])
	action := fieldText(value["action"])
	if rid == "" {
		return "无效操作", false, nil
	}
	records, err := a.fs.listRecords()
	if err != nil {
		return "查询失败", false, nil
	}
	rec := findByID(records, rid)
	if rec == nil {
		return "记录不存在或已删除", false, nil
	}
	status := fieldText(rec.Fields[FStatus])
	title := fieldText(rec.Fields[FTitle])
	switch action {
	case "confirm":
		if status != SConfirm {
			return "当前状态「" + status + "」，无需确认", false, nil
		}
		a.fs.updateRecord(rid, map[string]any{FStatus: SDev})
		return "✅ 已确认，开始开发", true, doneToastCard("✅ 已确认："+title, "已进入开发，完成后同步进度。", "blue")
	case "done":
		if status != SMerge {
			return "当前状态「" + status + "」", false, nil
		}
		a.fs.updateRecord(rid, map[string]any{FStatus: SDone})
		return "✅ 已标记完成", false, doneToastCard("✅ 已完成："+title, "需求已收尾。", "green")
	case "start_clarify":
		if status != SSetup {
			return "当前状态「" + status + "」，无需开始澄清", false, nil
		}
		a.fs.updateRecord(rid, map[string]any{FStatus: SClarify})
		agent := orDefault(fieldText(rec.Fields[FAgent]), cfg.EngineClarify)
		ws := orDefault(fieldText(rec.Fields[FWorkspace]), "默认")
		return "🚀 开始澄清", true, doneToastCard("🚀 开始澄清："+title, "将用 "+agent+" 在工作区 "+ws+" 澄清，请稍候。", "wathet")
	case "open_settings":
		return "打开配置", false, settingsCard(rec, workspaceKeys())
	case "set_agent":
		r := a.setAgent(rec, fieldText(value["agent"]), "")
		return r.msg, false, settingsCard(rec, workspaceKeys())
	case "set_workspace":
		r := a.setWorkspace(rec, fieldText(value["workspace"]))
		if !r.ok {
			return r.msg, false, nil
		}
		return r.msg, false, settingsCard(rec, workspaceKeys())
	}
	// 恢复类
	ops := map[string]func(*Record) opResult{
		"retry": a.retryRecord, "clear_lock": a.clearLock, "restart_clarify": a.restartClarify,
		"unblock_dev": func(r *Record) opResult { return a.unblockRecord(r, SDev) }, "mark_done": a.markDone,
	}
	if fn, ok := ops[action]; ok {
		r := fn(rec)
		tmpl := "green"
		prefix := "✅ "
		if !r.ok {
			tmpl = "yellow"
			prefix = "⚠️ "
		}
		return r.msg, r.dispatch, doneToastCard(prefix+title, r.msg, tmpl)
	}
	return "未知操作", false, nil
}
