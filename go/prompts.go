package main

import (
	"embed"
	"strings"
)

//go:embed prompts/*.md
var promptFS embed.FS

// buildPrompt 读 prompts/<stage>.md，把 {{key}} 占位符替换成实参。
func buildPrompt(stage string, kv map[string]string) string {
	b, err := promptFS.ReadFile("prompts/" + stage + ".md")
	if err != nil {
		return ""
	}
	out := string(b)
	for k, v := range kv {
		out = strings.ReplaceAll(out, "{{"+k+"}}", v)
	}
	return out
}
