package main

import "strings"

// routeClarify 稳健识别 clarify 输出的契约标记，容忍 CLEAR/QUESTIONS 前有少量前言行。
// 返回 (verdict, payload)，verdict ∈ {"CLEAR","QUESTIONS"}。
func routeClarify(out string) (string, string) {
	lines := strings.Split(strings.TrimSpace(out), "\n")
	limit := 6
	if len(lines) < limit {
		limit = len(lines)
	}
	for i := 0; i < limit; i++ {
		t := strings.ToUpper(strings.TrimSpace(lines[i]))
		if strings.HasPrefix(t, "CLEAR") {
			return "CLEAR", strings.TrimSpace(strings.Join(lines[i+1:], "\n"))
		}
		if strings.HasPrefix(t, "QUESTIONS") {
			return "QUESTIONS", strings.TrimSpace(strings.Join(lines[i+1:], "\n"))
		}
	}
	return "QUESTIONS", strings.TrimSpace(out)
}

// reviewVerdict 稳健识别 review 的 PASS/FAIL（容忍前言）；都没命中 → FAIL（保守）。
func reviewVerdict(out string) string {
	lines := strings.Split(strings.TrimSpace(out), "\n")
	limit := 6
	if len(lines) < limit {
		limit = len(lines)
	}
	for i := 0; i < limit; i++ {
		t := strings.ToUpper(strings.TrimSpace(lines[i]))
		if strings.HasPrefix(t, "PASS") {
			return "PASS"
		}
		if strings.HasPrefix(t, "FAIL") {
			return "FAIL"
		}
	}
	return "FAIL"
}
