#!/usr/bin/env bash
# Uninstall smartctx: the Python package (pipx or pip) and, on request,
# the per-profile and per-repo configuration it wrote.
set -euo pipefail

warn() { printf "smartctx: %s\n" "$*" >&2; }

confirm() {
    local prompt="$1"
    local answer
    read -r -p "$prompt [y/N] " answer
    [[ "$answer" =~ ^[yY]([eE][sS])?$ ]]
}

uninstall_pkg() {
    if command -v pipx >/dev/null 2>&1 \
        && pipx list --short 2>/dev/null | grep -Eq '^[[:space:]]*smartctx([[:space:]]|$)'; then
        warn "found smartctx installed via pipx — running: pipx uninstall smartctx"
        if ! pipx uninstall smartctx; then
            warn "pipx uninstall failed; remove it by hand, then re-run for config cleanup"
        fi
        return
    fi
    if command -v smartctx >/dev/null 2>&1; then
        warn "smartctx on PATH but not via pipx — trying pip instead"
        if ! python3 -m pip uninstall -y smartctx; then
            warn "automatic uninstall failed; do it by hand from the environment that provides"
            warn "  $(command -v smartctx)"
        fi
        return
    fi
    warn "no smartctx install found (pipx or pip); skipping package removal"
}

cleanup_config() {
    local config_dir
    if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
        config_dir="${CLAUDE_CONFIG_DIR/#\~/$HOME}"
    else
        config_dir="$HOME/.claude"
    fi
    local user_cfg="$config_dir/smartctx"
    local repo_cfg="./.smartctx"

    if [[ -d "$user_cfg" ]] && confirm "remove $user_cfg (config.toml, rules.toml)? "; then
        rm -rf "$user_cfg"
        warn "removed $user_cfg"
    fi
    if [[ -d "$repo_cfg" ]] && confirm "remove $repo_cfg (this repo's .smartctx/)? "; then
        rm -rf "$repo_cfg"
        warn "removed $repo_cfg"
    fi
}

warn "uninstalling smartctx"
uninstall_pkg
cleanup_config
cat >&2 <<'EOF'
smartctx: done. Remaining by hand:
  - remove any aliases you added to your shell rc (e.g. alias claude-work="... smartctx")
  - optionally drop the now-stale ~/.claude-work / ~/.claude-perso profile dirs
EOF
