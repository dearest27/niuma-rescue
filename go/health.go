package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

var healthMu sync.Mutex

func stateDir() string {
	if cfg != nil && cfg.StateDir != "" {
		return cfg.StateDir
	}
	return "state"
}

// emit 写组件最新状态 + 追加一行事件。Best-effort：埋点绝不能拖垮主流程。
func emit(component, event string, fields map[string]any) {
	healthMu.Lock()
	defer healthMu.Unlock()
	dir := stateDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return
	}
	payload := map[string]any{
		"ts":        float64(time.Now().UnixNano()) / 1e9,
		"time":      time.Now().Format("2006-01-02 15:04:05 -0700"),
		"component": component,
		"event":     event,
		"pid":       os.Getpid(),
	}
	for k, v := range fields {
		payload[k] = v
	}
	b, err := json.Marshal(payload)
	if err != nil {
		return
	}
	// 最新状态（覆盖写）
	latest := filepath.Join(dir, component+".json")
	tmp := latest + ".tmp"
	if os.WriteFile(tmp, b, 0o644) == nil {
		os.Rename(tmp, latest)
	}
	// 事件流（追加）
	if f, err := os.OpenFile(filepath.Join(dir, "events.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
		f.Write(append(b, '\n'))
		f.Close()
	}
}

func readAll() map[string]map[string]any {
	out := map[string]map[string]any{}
	entries, err := os.ReadDir(stateDir())
	if err != nil {
		return out
	}
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") || strings.HasSuffix(e.Name(), ".tmp") {
			continue
		}
		b, err := os.ReadFile(filepath.Join(stateDir(), e.Name()))
		if err != nil {
			continue
		}
		var m map[string]any
		if json.Unmarshal(b, &m) == nil {
			out[strings.TrimSuffix(e.Name(), ".json")] = m
		}
	}
	return out
}

func readEvents(maxLines int) []map[string]any {
	b, err := os.ReadFile(filepath.Join(stateDir(), "events.jsonl"))
	if err != nil {
		return nil
	}
	lines := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	if len(lines) > maxLines {
		lines = lines[len(lines)-maxLines:]
	}
	var out []map[string]any
	for _, ln := range lines {
		var m map[string]any
		if json.Unmarshal([]byte(ln), &m) == nil {
			out = append(out, m)
		}
	}
	return out
}

func summaryText(hours float64) string {
	now := float64(time.Now().UnixNano()) / 1e9
	cutoff := now - hours*3600
	evs := readEvents(20000)
	var durs []float64
	byEngine := map[string]int{}
	kills, timeouts, gateOK, gateFail := 0, 0, 0, 0
	trans := map[string]int{}
	for _, e := range evs {
		ts, _ := e["ts"].(float64)
		if ts < cutoff {
			continue
		}
		switch e["event"] {
		case "agent_done":
			if d, ok := e["duration"].(float64); ok {
				durs = append(durs, d)
			}
			byEngine[fmt.Sprintf("%v", e["engine"])]++
		case "agent_inactive_kill":
			kills++
		case "agent_timeout":
			timeouts++
		case "gate_done":
			if ok, _ := e["ok"].(bool); ok {
				gateOK++
			} else {
				gateFail++
			}
		case "transition":
			trans[fmt.Sprintf("%v", e["to"])]++
		}
	}
	avg, total := 0.0, 0.0
	for _, d := range durs {
		total += d
	}
	if len(durs) > 0 {
		avg = total / float64(len(durs))
	}
	var engs []string
	for k, v := range byEngine {
		engs = append(engs, fmt.Sprintf("%s %d", k, v))
	}
	sort.Strings(engs)
	eng := strings.Join(engs, "、")
	if eng == "" {
		eng = "无"
	}
	lines := []string{
		fmt.Sprintf("📊 最近 %dh 运行报表", int(hours)),
		fmt.Sprintf("· agent 调用 %d 次（%s）", len(durs), eng),
		fmt.Sprintf("· 平均耗时 %.1fs · 累计 %dm", avg, int(total)/60),
		fmt.Sprintf("· 卡死自愈 %d 次 · 超时 %d 次", kills, timeouts),
		fmt.Sprintf("· 验收门 通过 %d / 失败 %d", gateOK, gateFail),
	}
	if len(trans) > 0 {
		lines = append(lines, fmt.Sprintf("· 流转：完成 %d · 阻塞 %d · 进入Review %d",
			trans[SDone], trans[SBlocked], trans[SReview]))
	}
	return strings.Join(lines, "\n")
}

func dur(sec float64) string {
	s := int(sec)
	if s < 0 {
		s = 0
	}
	if s < 60 {
		return fmt.Sprintf("%d秒", s)
	}
	if s < 3600 {
		return fmt.Sprintf("%d分%02d秒", s/60, s%60)
	}
	return fmt.Sprintf("%d时%02d分", s/3600, (s%3600)/60)
}

// systemHealthText 部署自查：listener/dispatcher 最近活动 + 卡住的 run。
func systemHealthText(st *Store) string {
	now := float64(time.Now().UnixNano()) / 1e9
	states := readAll()
	line := func(comp, label string, okWithin float64) string {
		s := states[comp]
		if s == nil {
			return fmt.Sprintf("· %s：❓ 无活动记录（可能从未启动）", label)
		}
		ts, _ := s["ts"].(float64)
		age := now - ts
		mark := "✅"
		if age > okWithin {
			mark = "⚠️ 可能没在跑"
		}
		return fmt.Sprintf("· %s：%s 最近活动 %s前（%v）", label, mark, dur(age), s["event"])
	}
	lines := []string{
		"🩺 系统健康",
		line("listener", "监听(收消息)", 180),
		line("dispatcher", "调度(跑任务)", float64(cfg.PollInterval*2+120)),
	}
	if st != nil {
		procs := st.listRuns(50, "processing")
		if len(procs) > 0 {
			var parts []string
			for i, p := range procs {
				if i >= 5 {
					break
				}
				parts = append(parts, fmt.Sprintf("%s/%s(%s)", short(p.RecordID, 8), p.Stage, dur(now-p.HeartbeatAt)))
			}
			lines = append(lines, fmt.Sprintf("· 正在处理 %d 个：%s", len(procs), strings.Join(parts, "；")))
		} else {
			lines = append(lines, "· 当前无正在处理的任务")
		}
	}
	return strings.Join(lines, "\n")
}

func short(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
