package main

import (
	"bufio"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

type AgentResult struct {
	OK       bool
	Output   string
	Duration float64
}

var baseErrorMarkers = []string{
	"invalid authentication", "failed to authenticate", "authentication failed",
	"not authenticated", "api error", "401", "rate limit", "quota",
}
var engineErrorMarkers = map[string][]string{
	"claude": {"claude code is not authenticated", "please run /login", "anthropic_auth_token"},
	"codex":  {"not logged in", "please login", "approval denied"},
	"gemini": {"please login", "google api key", "permission denied"},
	"cursor": {"cursor agent is not authenticated", "login required", "workspace is not trusted"},
}

// sink 累积 agent 输出：cursor 用 stream-json 事件，其余引擎用裸文本。
type sink interface {
	feed(tag, line string)
	progress() map[string]any
	finalText() string
	errorBlob() string
	isError() bool
}

type rawSink struct{ out, err []string }

func (s *rawSink) feed(tag, line string) {
	if tag == "err" {
		s.err = append(s.err, line)
	} else {
		s.out = append(s.out, line)
	}
}
func (s *rawSink) progress() map[string]any {
	n := 0
	for _, l := range s.out {
		n += len(l)
	}
	return map[string]any{"output_len": n}
}
func (s *rawSink) finalText() string { return strings.TrimSpace(strings.Join(s.out, "\n")) }
func (s *rawSink) errorBlob() string {
	return strings.Join(s.out, "\n") + "\n" + strings.Join(s.err, "\n")
}
func (s *rawSink) isError() bool { return false }

type cursorSink struct {
	resultText string
	hasResult  bool
	isErr      bool
	assistant  []string
	errLines   []string
	events     int
	thinking   int
	toolCalls  int
}

func (s *cursorSink) feed(tag, line string) {
	if tag == "err" {
		if t := strings.TrimSpace(line); t != "" {
			s.errLines = append(s.errLines, t)
		}
		return
	}
	t := strings.TrimSpace(line)
	if t == "" {
		return
	}
	if !strings.HasPrefix(t, "{") {
		s.assistant = append(s.assistant, line)
		return
	}
	var d map[string]any
	if json.Unmarshal([]byte(t), &d) != nil {
		s.assistant = append(s.assistant, line)
		return
	}
	s.events++
	switch d["type"] {
	case "thinking":
		s.thinking++
	case "tool_call":
		if d["subtype"] == "started" {
			s.toolCalls++
		}
	case "assistant":
		if msg, ok := d["message"].(map[string]any); ok {
			if content, ok := msg["content"].([]any); ok {
				for _, b := range content {
					if blk, ok := b.(map[string]any); ok && blk["type"] == "text" {
						if txt, _ := blk["text"].(string); strings.TrimSpace(txt) != "" {
							s.assistant = append(s.assistant, txt)
						}
					}
				}
			}
		}
	case "result":
		if rt, ok := d["result"].(string); ok {
			s.resultText = rt
		}
		s.hasResult = true
		s.isErr, _ = d["is_error"].(bool)
	}
}
func (s *cursorSink) progress() map[string]any {
	return map[string]any{"events": s.events, "thinking": s.thinking, "tool_calls": s.toolCalls}
}
func (s *cursorSink) finalText() string {
	if strings.TrimSpace(s.resultText) != "" {
		return s.resultText
	}
	return strings.TrimSpace(strings.Join(s.assistant, "\n"))
}
func (s *cursorSink) errorBlob() string { return strings.Join(s.errLines, "\n") }
func (s *cursorSink) isError() bool     { return s.hasResult && s.isErr }

func agentArgv(engine string) []string {
	base, ok := AgentCmds[engine]
	if !ok {
		return nil
	}
	// 尊重配置里的 --output-format：stream-json 走事件解析 + 活跃度看门狗；text 走裸文本。
	// 注意 cursor 的 composer-2.5（非 fast）只在 text 模式可用，stream-json 会被拒。
	return append([]string{}, base...)
}

func hasStreamJSON(argv []string) bool {
	for _, a := range argv {
		if a == "stream-json" {
			return true
		}
	}
	return false
}

func scrubbedEnv() []string {
	var out []string
	for _, kv := range os.Environ() {
		k := kv
		if i := strings.IndexByte(kv, '='); i >= 0 {
			k = kv[:i]
		}
		if ScrubEnvKeys[k] {
			continue
		}
		skip := false
		for _, p := range ScrubEnvPrefixes {
			if strings.HasPrefix(k, p) {
				skip = true
				break
			}
		}
		if !skip {
			out = append(out, kv)
		}
	}
	return out
}

func validate(engine string, rc int, output string) AgentResult {
	if rc != 0 {
		return AgentResult{OK: false, Output: output}
	}
	if strings.TrimSpace(output) == "" {
		return AgentResult{OK: false, Output: engine + " 返回空输出"}
	}
	lower := strings.ToLower(output)
	markers := append(append([]string{}, baseErrorMarkers...), engineErrorMarkers[engine]...)
	for _, m := range markers {
		if strings.Contains(lower, m) {
			return AgentResult{OK: false, Output: engine + " 输出疑似错误: " + trunc(output, 300)}
		}
	}
	return AgentResult{OK: true, Output: output}
}

type tagLine struct{ tag, line string }

// 瞬时错误（多为 cursor 的网络/TLS 抖动）——立即重试即可，不该判失败更不该计入熔断。
var transientRe = regexp.MustCompile(`(?i)aborted|socket disconnected|retriableerror|secure tls|econnreset|connection reset|broken pipe|bad gateway|service unavailable|temporarily unavailable|i/o timeout|\b50[234]\b`)

// runAgent：在 runAgentOnce 外套一层"瞬时错误自动重试"。
// 网络/TLS 类报错立即重试 AgentRetries 次（短退避），把大多数 cursor 抖动悄悄吞掉。
func runAgent(engine, prompt, cwd string, timeout int, lg func(string), onProgress func(map[string]any)) AgentResult {
	tries := cfg.AgentRetries + 1
	if tries < 1 {
		tries = 1
	}
	var res AgentResult
	for try := 1; try <= tries; try++ {
		res = runAgentOnce(engine, prompt, cwd, timeout, lg, onProgress)
		if res.OK || !transientRe.MatchString(res.Output) {
			return res
		}
		if try < tries {
			backoff := time.Duration(3*try) * time.Second
			emit("dispatcher", "agent_transient_retry", map[string]any{"engine": engine, "try": try})
			if lg != nil {
				lg("  " + engine + " 瞬时错误(第" + itoa(try) + "次)，" + itoa(3*try) + "s 后重试：" + trunc(res.Output, 100))
			}
			time.Sleep(backoff)
		}
	}
	return res
}

// runAgentOnce 单次执行：Popen + 读取 goroutine + 看门狗。
// 总超时兜底；无输出超 Inactivity 即判卡死杀掉——但出过首行输出后才武装（沉默到底的引擎不误杀）。
// 按时间触发 onProgress 心跳。
func runAgentOnce(engine, prompt, cwd string, timeout int, lg func(string), onProgress func(map[string]any)) AgentResult {
	argv := agentArgv(engine)
	if argv == nil {
		return AgentResult{OK: false, Output: "unknown agent: " + engine}
	}
	if exe, err := exec.LookPath(argv[0]); err == nil {
		argv[0] = exe
	}
	inactivity := cfg.Inactivity
	emit("dispatcher", "agent_start", map[string]any{"engine": engine, "cwd": cwd, "timeout": timeout, "inactivity": inactivity})
	if lg != nil {
		lg("  调用 " + engine + ": " + strings.Join(argv, " ") + " (timeout=" + itoa(timeout) + "s)…")
	}

	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = cwd
	cmd.Env = scrubbedEnv()
	stdin, _ := cmd.StdinPipe()
	stdout, _ := cmd.StdoutPipe()
	stderr, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		emit("dispatcher", "agent_command_missing", map[string]any{"engine": engine, "command": argv[0]})
		if lg != nil {
			lg("  找不到命令 `" + argv[0] + "` —— 该引擎 CLI 没装或不在 PATH")
		}
		return AgentResult{OK: false, Output: "command not found: " + argv[0]}
	}
	go func() { io.WriteString(stdin, prompt); stdin.Close() }()

	lines := make(chan tagLine, 512)
	reader := func(r io.Reader, tag string, done chan<- struct{}) {
		sc := bufio.NewScanner(r)
		sc.Buffer(make([]byte, 64*1024), 8*1024*1024)
		for sc.Scan() {
			lines <- tagLine{tag, sc.Text()}
		}
		done <- struct{}{}
	}
	rdone := make(chan struct{}, 2)
	go reader(stdout, "out", rdone)
	go reader(stderr, "err", rdone)
	go func() { <-rdone; <-rdone; close(lines) }()

	var sk sink
	if hasStreamJSON(argv) {
		sk = &cursorSink{} // stream-json 事件解析
	} else {
		sk = &rawSink{} // 裸文本（cursor 用 composer-2.5/text 时走这）
	}
	start := time.Now()
	lastActivity := start
	seenOutput := false
	var lastProg float64
	killed := ""
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()

loop:
	for {
		select {
		case ln, ok := <-lines:
			if !ok {
				break loop // stdout+stderr 都 EOF → 输出结束
			}
			lastActivity = time.Now()
			seenOutput = true
			sk.feed(ln.tag, ln.line)
		case <-ticker.C:
			now := time.Now()
			if now.Sub(start).Seconds() > float64(timeout) {
				killed = "timeout"
				break loop
			}
			if seenOutput && inactivity > 0 && now.Sub(lastActivity).Seconds() > float64(inactivity) {
				killed = "inactivity"
				break loop
			}
			if onProgress != nil && now.Sub(start).Seconds()-lastProg >= 1.0 {
				lastProg = now.Sub(start).Seconds()
				p := sk.progress()
				p["elapsed"] = int(now.Sub(start).Seconds())
				onProgress(p)
			}
		}
	}
	duration := time.Since(start).Seconds()

	if killed != "" {
		_ = cmd.Process.Kill()
		go cmd.Wait()
		event := "agent_timeout"
		msg := engine + " timed out after " + itoa(timeout) + "s"
		if killed == "inactivity" {
			event = "agent_inactive_kill"
			msg = engine + " 无输出超过 " + itoa(inactivity) + "s（疑似卡死），已终止"
		}
		emit("dispatcher", event, map[string]any{"engine": engine, "timeout": timeout, "inactivity": inactivity, "duration": round1(duration)})
		if lg != nil {
			lg("  " + msg)
		}
		return AgentResult{OK: false, Output: msg, Duration: duration}
	}

	cmd.Wait()
	rc := cmd.ProcessState.ExitCode()
	text := sk.finalText()
	blob := text
	if blob == "" {
		blob = sk.errorBlob()
	}
	emit("dispatcher", "agent_done", map[string]any{"engine": engine, "returncode": rc, "duration": round1(duration), "output_len": len(blob)})
	if lg != nil {
		lg("  " + engine + " 退出码=" + itoa(rc) + "，耗时 " + itoa(int(duration)) + "s，输出 " + itoa(len(blob)) + " 字")
	}
	var res AgentResult
	if sk.isError() {
		res = AgentResult{OK: false, Output: engine + " 报错: " + trunc(blob, 300)}
	} else {
		res = validate(engine, rc, blob)
	}
	res.Duration = duration
	return res
}
