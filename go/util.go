package main

import (
	"fmt"
	"os"
	"strings"
	"time"
)

func errf(format string, a ...any) error { return fmt.Errorf(format, a...) }

func itoa(n int) string { return fmt.Sprintf("%d", n) }

func deref(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

// cut 截断（不加省略号），用于日志。
func cut(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

func round1(f float64) float64 { return float64(int(f*10+0.5)) / 10 }

// logf 带时间戳打到 stdout（被 launchd/systemd 收进日志）。
func logf(format string, a ...any) {
	fmt.Printf("[%s] %s\n", time.Now().Format("15:04:05"), fmt.Sprintf(format, a...))
}

// elog 打到 stderr。
func elog(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "[listener] %s\n", fmt.Sprintf(format, a...))
}

// fieldText 把飞书字段值归一成字符串，兼容单选/多选/人员等不同返回形状。
func fieldText(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return strings.TrimSpace(t)
	case float64:
		// 整数别带 .0
		if t == float64(int64(t)) {
			return fmt.Sprintf("%d", int64(t))
		}
		return fmt.Sprintf("%v", t)
	case bool:
		return fmt.Sprintf("%v", t)
	case []any:
		if len(t) > 0 {
			return fieldText(t[0])
		}
		return ""
	case map[string]any:
		for _, k := range []string{"text", "name", "value", "id"} {
			if s, ok := t[k]; ok && s != nil {
				return strings.TrimSpace(fmt.Sprintf("%v", s))
			}
		}
		return ""
	default:
		return strings.TrimSpace(fmt.Sprintf("%v", t))
	}
}

func fieldInt(v any) int {
	switch t := v.(type) {
	case float64:
		return int(t)
	case int:
		return t
	case string:
		var n int
		fmt.Sscanf(t, "%d", &n)
		return n
	default:
		return 0
	}
}

func trunc(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…（略）"
}

// tail 取末尾 n 个字符（按 rune）。
func tail(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[len(r)-n:])
}

// toStrList 把任意 JSON 值归一成字符串切片（飞书表单多选返回 []any{string}）。
func toStrList(v any) []string {
	var out []string
	switch t := v.(type) {
	case []any:
		for _, x := range t {
			if s := strings.TrimSpace(fmt.Sprintf("%v", x)); s != "" {
				out = append(out, s)
			}
		}
	case []string:
		out = t
	case string:
		if s := strings.TrimSpace(t); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func firstLineToken(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}
