package main

import (
	"bufio"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// 飞书字段名（系统契约，必须与 Base 列名一致；默认与 Python 版相同）。
const (
	FTitle        = "需求标题"
	FStatus       = "状态"
	FDesc         = "需求描述"
	FClarify      = "澄清记录"
	FPRD          = "PRD"
	FLink         = "分支PR链接"
	FLog          = "执行日志"
	FFails        = "失败次数"
	FOwner        = "提需求人"
	FChat         = "会话ID"
	FWorkspace    = "工作区"
	FAgent        = "执行Agent"
	FAgentClarify = "澄清Agent"
	FAgentCode    = "开发Agent"
	FAgentReview  = "ReviewAgent"
)

// 状态值（单选选项）。
const (
	SSetup   = "待选择"
	SClarify = "待澄清"
	SAnswer  = "待回答"
	SConfirm = "待确认"
	SDev     = "开发中"
	SReview  = "Review中"
	SMerge   = "待合并"
	SDone    = "完成"
	SBlocked = "已阻塞"
)

// dispatcher 只处理这三个；其余是人工卡点或终态。
var Actionable = map[string]bool{SClarify: true, SDev: true, SReview: true}

// 等待人在飞书里输入的状态。
var HumanInput = map[string]bool{SAnswer: true, SConfirm: true}

// dispatcher 允许自动推进的状态边。
var ValidTransitions = map[string]map[string]bool{
	SSetup:   {SClarify: true},
	SClarify: {SAnswer: true, SConfirm: true, SBlocked: true},
	SDev:     {SReview: true, SBlocked: true},
	SReview:  {SDev: true, SMerge: true, SBlocked: true},
}

var IntakePrefixes = []string{"需求"}
var ConfirmWords = []string{"确认", "ok", "确定", "可以开发"}

// 各引擎 headless 命令模板，prompt 走 stdin。
var AgentCmds = map[string][]string{
	"claude": {"claude", "-p", "--output-format", "text"},
	"codex":  {"codex", "exec", "-"},
	"gemini": {"gemini", "--skip-trust", "--approval-mode", "yolo", "-p", " "},
	"cursor": {"cursor-agent", "--print", "--force", "--trust", "--model", "composer-2.5", "--output-format", "text"},
}

// 中文/大小写/别名 → AGENT_CMDS 的 key。
var AgentAliases = map[string]string{
	"claude": "claude", "claude code": "claude",
	"codex": "codex", "openai codex": "codex",
	"gemini": "gemini",
	"cursor": "cursor", "cursor-agent": "cursor", "cursor agent": "cursor",
}

// 启动 agent 子进程前剔除这些污染认证的环境变量。
var ScrubEnvKeys = map[string]bool{"ANTHROPIC_BASE_URL": true, "CLAUDECODE": true, "ANTHROPIC_AUTH_TOKEN": true}
var ScrubEnvPrefixes = []string{"CLAUDE_CODE_", "CLAUDE_PLUGIN", "AI_AGENT"}

// 验收门：从输出里数"错误行"。
var GateErrorRe = regexp.MustCompile(`(?i)\berror\b|✖|\bfailed\b`)
var CodeExts = []string{".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".go", ".rb", ".rs", ".php", ".cs", ".kt", ".c", ".cpp", ".h"}

// Config 持有部署相关、可由 .env 覆盖的值。
type Config struct {
	AppID, AppSecret                        string
	BaseToken, TableID                      string
	RepoPath, BaseRef                       string
	GHRepo, TestCmd                         string
	EngineClarify, EngineCode, EngineReview string

	TimeoutClarify, TimeoutCode, TimeoutReview          int
	Inactivity, ProgressInterval, StaleAfter, RetryBase int
	FailureLimit, PollInterval, MaxConcurrency          int
	AgentRetries                                        int
	SetupGate, GateRelative, PushEnabled, PREnabled     bool

	Root, StateDir, WorktreeBase, WorkspacesFile string
}

var cfg *Config

const dossierDir = ".pipeline"

func loadConfig() *Config {
	root := loadDotenv()
	c := &Config{
		AppID:     os.Getenv("FEISHU_APP_ID"),
		AppSecret: os.Getenv("FEISHU_APP_SECRET"),
		BaseToken: os.Getenv("PIPELINE_BASE_TOKEN"),
		TableID:   os.Getenv("PIPELINE_TABLE_ID"),
		RepoPath:  os.Getenv("PIPELINE_REPO_PATH"),
		BaseRef:   envText("PIPELINE_BASE_REF", "origin/main"),
		GHRepo:    os.Getenv("PIPELINE_GH_REPO"),
		TestCmd:   os.Getenv("PIPELINE_TEST_CMD"),

		EngineClarify: envText("PIPELINE_ENGINE_CLARIFY", "claude"),
		EngineCode:    envText("PIPELINE_ENGINE_CODE", "cursor"),
		EngineReview:  envText("PIPELINE_ENGINE_REVIEW", "gemini"),

		TimeoutClarify:   envInt("PIPELINE_TIMEOUT_CLARIFY", 600),
		TimeoutCode:      envInt("PIPELINE_TIMEOUT_CODE", 1800),
		TimeoutReview:    envInt("PIPELINE_TIMEOUT_REVIEW", 900),
		Inactivity:       envInt("PIPELINE_INACTIVITY_TIMEOUT", 120),
		ProgressInterval: envInt("PIPELINE_PROGRESS_INTERVAL", 20),
		StaleAfter:       envInt("PIPELINE_EXECUTION_STALE_AFTER", 600),
		RetryBase:        envInt("PIPELINE_RETRY_BASE_DELAY", 60),
		FailureLimit:     envInt("PIPELINE_FAILURE_LIMIT", 2),
		PollInterval:     envInt("PIPELINE_POLL_INTERVAL", 900),
		MaxConcurrency:   envInt("PIPELINE_MAX_CONCURRENCY", 2),
		AgentRetries:     envInt("PIPELINE_AGENT_RETRIES", 2),

		SetupGate:    envBool("PIPELINE_SETUP_GATE", true),
		GateRelative: envBool("PIPELINE_GATE_RELATIVE", true),
		PushEnabled:  envBool("PIPELINE_PUSH_ENABLED", false),
		PREnabled:    envBool("PIPELINE_PR_ENABLED", false),

		Root: root,
	}
	c.StateDir = envText("PIPELINE_STATE_DIR", filepath.Join(root, "state"))
	c.WorktreeBase = envText("PIPELINE_WORKTREE_BASE", filepath.Join(root, "worktrees"))
	c.WorkspacesFile = envText("PIPELINE_WORKSPACES_FILE", filepath.Join(root, "workspaces.json"))
	if ce := os.Getenv("PIPELINE_CODE_EXTS"); strings.TrimSpace(ce) != "" {
		var exts []string
		for _, e := range strings.Split(ce, ",") {
			if e = strings.TrimSpace(e); e != "" {
				exts = append(exts, e)
			}
		}
		if len(exts) > 0 {
			CodeExts = exts
		}
	}
	return c
}

func (c *Config) agentTimeout() int {
	m := c.TimeoutClarify
	if c.TimeoutCode > m {
		m = c.TimeoutCode
	}
	if c.TimeoutReview > m {
		m = c.TimeoutReview
	}
	return m
}

func (c *Config) validate() error {
	var missing []string
	if c.BaseToken == "" {
		missing = append(missing, "PIPELINE_BASE_TOKEN")
	}
	if c.TableID == "" {
		missing = append(missing, "PIPELINE_TABLE_ID")
	}
	if c.RepoPath == "" {
		missing = append(missing, "PIPELINE_REPO_PATH")
	}
	if len(missing) > 0 {
		return errf("缺少必填配置 %v：在 .env 设置（参考 .env.example）", missing)
	}
	return nil
}

// loadDotenv 在 NIUMA_ENV / ./.env / ../.env / ../../.env 里找 .env 灌进环境，
// 返回 .env 所在目录作为项目根（state/worktrees/workspaces 都锚在这）。
func loadDotenv() string {
	candidates := []string{}
	if p := os.Getenv("NIUMA_ENV"); p != "" {
		candidates = append(candidates, p)
	}
	candidates = append(candidates, ".env", filepath.Join("..", ".env"), filepath.Join("..", "..", ".env"))
	for _, p := range candidates {
		abs, err := filepath.Abs(p)
		if err != nil {
			continue
		}
		f, err := os.Open(abs)
		if err != nil {
			continue
		}
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			line := strings.TrimSpace(sc.Text())
			if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
				continue
			}
			kv := strings.SplitN(line, "=", 2)
			k := strings.TrimSpace(kv[0])
			if k != "" {
				if _, ok := os.LookupEnv(k); !ok {
					v := strings.TrimSpace(kv[1])
					v = strings.Trim(v, `"'`)
					os.Setenv(k, v)
				}
			}
		}
		f.Close()
		return filepath.Dir(abs)
	}
	wd, _ := os.Getwd()
	return wd
}

func envText(name, def string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return def
}

func envInt(name string, def int) int {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envBool(name string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
	if v == "" {
		return def
	}
	switch v {
	case "1", "true", "yes", "y", "on":
		return true
	default:
		return false
	}
}

// normalizeAgent 把别名归一到 AGENT_CMDS 的 key（找不到返回原值小写）。
func normalizeAgent(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	if e, ok := AgentAliases[raw]; ok {
		return e
	}
	if e, ok := AgentAliases[strings.ToLower(raw)]; ok {
		return e
	}
	return strings.ToLower(raw)
}
