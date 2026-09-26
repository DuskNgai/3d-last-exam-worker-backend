#!/bin/sh
# Claude Code status line with separator layout
input=$(cat)

esc=$(printf '\033')
R="${esc}[0m"
BoldCyan="${esc}[1;36m"
BoldBlue="${esc}[1;34m"
Magenta="${esc}[0;35m"
Red="${esc}[0;31m"
Yellow="${esc}[0;33m"
Dim="${esc}[2m"

cwd=$(echo "$input" | jq -r '.cwd // .workspace.current_dir // empty')
dir=$(basename "$cwd")
model=$(echo "$input" | jq -r '.model.display_name // "?"')
effort=$(echo "$input" | jq -r '.effort.level // empty')
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')

# ── Git ──────────────────────────────────────────────────────────────
git_part=""
if git -C "$cwd" rev-parse --git-dir -q 2>/dev/null | grep -q .; then
  branch=$(git -C "$cwd" -c gc.auto=0 symbolic-ref --short HEAD 2>/dev/null || git -C "$cwd" rev-parse --short HEAD 2>/dev/null)
  if [ -n "$branch" ]; then
    dirty=$(git -C "$cwd" -c gc.auto=0 status --porcelain 2>/dev/null)
    if [ -n "$dirty" ]; then
      git_part="${BoldBlue}git:(${Red}${branch}${BoldBlue}) ${Yellow}✗${R}"
    else
      git_part="${BoldBlue}git:(${Red}${branch}${BoldBlue})${R}"
    fi
  fi
fi

# ── Separator ────────────────────────────────────────────────────────
SEP=" ${Dim}|${R} "

# ── Context ──────────────────────────────────────────────────────────
used="${used:-0}"
if [ "$used" -ge 80 ]; then
  ctx_part="${Red}ctx:${used}%${R}"
elif [ "$used" -ge 60 ]; then
  ctx_part="${Yellow}ctx:${used}%${R}"
else
  ctx_part="${Dim}ctx:${used}%${R}"
fi

# ── Model ────────────────────────────────────────────────────────────
model_part="${Magenta}${model}${R}"
if [ -n "$effort" ]; then
  model_part="${Magenta}${model} (${effort})${R}"
fi

# ── Assembly ──────────────────────────────────────────────────────────
parts="${BoldCyan}${dir}${R}"

if [ -n "$git_part" ]; then
  parts="${parts}${SEP}${git_part}"
fi

parts="${parts}${SEP}${model_part}"

if [ -n "$ctx_part" ]; then
  parts="${parts}${SEP}${ctx_part}"
fi

printf "%s" "$parts"
