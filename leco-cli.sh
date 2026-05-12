#!/usr/bin/env bash

# ============================================
# LEco DevOps — unified management CLI
# ============================================
# Single entrypoint for managing the LEco DevOps Open Project on a developer
# machine: foundation install, ecosystem-stack services (Traefik, Postgres,
# Ollama, Open WebUI, n8n, Dashboard, Cloudflare-local, infra), hosted apps
# via `leco-app`, Traefik routing, the local registry, diagnostics, and
# common URLs.
#
# Modeled on CrawlerVision's cv-deploy.sh (interactive menu + direct command
# dispatcher). Wraps the existing scripts under ecosystem-stack/ and the
# `leco-app` (leco-devops) CLI rather than duplicating their logic.
#
# Run without arguments for the interactive menu, or pass a command (see
# `leco-cli.sh help`).

# `set -e` would abort menu loops on the first non-zero subcommand (docker ps
# of a missing container, leco-app exit codes, etc.) which makes the
# interactive flow unusable. Keep it off and check status codes per call.

# ---------- Colors -------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# ---------- Repo root + key paths ---------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
ECO_STACK_DIR="$PROJECT_ROOT/ecosystem-stack"
ECO_STACK_SH="$ECO_STACK_DIR/ecosystem-stack.sh"
ECO_SERVICES_DIR="$ECO_STACK_DIR/services"
ECO_INSTALL_SH="$ECO_STACK_DIR/install-foundation.sh"
ECO_CORE_SH="$ECO_STACK_DIR/core.sh"
REGISTRY_FILE="$PROJECT_ROOT/config/leco-registry.yaml"
HOSTING_APPS_DIR="$PROJECT_ROOT/hosting/app-available"
HOSTING_TRAEFIK_DIR="$PROJECT_ROOT/hosting/traefik"
CORE_TRAEFIK_DYNAMIC="$PROJECT_ROOT/traefik/dynamic.yml"
HOSTING_TRAEFIK_DYNAMIC="$HOSTING_TRAEFIK_DIR/dynamic.yml"
DASHBOARD_HOST_PORT="${DASHBOARD_HOST_PORT:-8090}"
ACTIVITY_LOG_DIR="$PROJECT_ROOT/.leco-cli-logs"
ACTIVITY_LOG_FILE=""

# Ordered service list (matches START_ORDER in ecosystem-stack/core.sh + infra).
SERVICES_ORDER="traefik postgres ollama webui n8n dashboard cloudflare-local infra"

# ---------- Activity log (best-effort JSONL) ----------------------
activity_init() {
    [ -d "$ACTIVITY_LOG_DIR" ] || mkdir -p "$ACTIVITY_LOG_DIR" 2>/dev/null || return 0
    local today
    today=$(date +%Y-%m-%d 2>/dev/null) || today="unknown"
    ACTIVITY_LOG_FILE="$ACTIVITY_LOG_DIR/leco-cli-${today}.jsonl"
}

activity_log() {
    [ -n "$ACTIVITY_LOG_FILE" ] || activity_init
    [ -n "$ACTIVITY_LOG_FILE" ] || return 0
    local action="${1:-unknown}" status="${2:-info}" detail="${3:-}"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "unknown")
    detail=$(printf '%s' "$detail" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g; s/\r/\\r/g')
    printf '{"ts":"%s","action":"%s","status":"%s","detail":"%s"}\n' \
        "$ts" "$action" "$status" "$detail" \
        >>"$ACTIVITY_LOG_FILE" 2>/dev/null || true
}

# ---------- Display helpers ---------------------------------------
show_header() {
    clear
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║                                                            ║"
    echo "║   ⚙️  LEco DevOps — Management CLI                          ║"
    echo "║   Repo: ${PROJECT_ROOT}"
    echo "║                                                            ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo -e "  ${DIM}leco-app: $(command -v leco-app 2>/dev/null || echo 'not installed')${NC}"
    echo -e "  ${DIM}docker:   $(command -v docker 2>/dev/null || echo 'not installed')${NC}"
    echo ""
}

show_menu() {
    echo -e "${WHITE}Select:${NC}"
    echo ""
    echo -e "  ${WHITE}Status & Setup${NC}"
    echo -e "    ${GREEN}1)${NC}  📊 Status (stack + hosted apps overview)"
    echo -e "    ${GREEN}2)${NC}  🛠️  Foundation install (deps, certs, network)"
    echo ""
    echo -e "  ${WHITE}Core stack${NC}"
    echo -e "    ${GREEN}3)${NC}  🏗️  Manage ecosystem services (traefik/postgres/…)"
    echo -e "    ${GREEN}4)${NC}  🖥️  LEco Dashboard (service-dashboard)"
    echo -e "    ${GREEN}5)${NC}  🌐 Traefik & *.lh routing"
    echo -e "    ${GREEN}6)${NC}  ☁️  Cloudflare-local (KV/R2/D1/Workers adapters)"
    echo -e "    ${GREEN}7)${NC}  🦙 Ollama models"
    echo ""
    echo -e "  ${WHITE}Hosted applications${NC}"
    echo -e "    ${GREEN}8)${NC}  📦 Hosted apps (leco-app)"
    echo ""
    echo -e "  ${WHITE}Helpers${NC}"
    echo -e "    ${GREEN}9)${NC}  🔗 Open service URLs (browser)"
    echo -e "    ${GREEN}10)${NC} 🩺 Diagnostics / repair (network, Traefik)"
    echo ""
    echo -e "  ${WHITE}Reference${NC}"
    echo -e "    ${GREEN}11)${NC} ❓ Help (detailed)"
    echo -e "    ${GREEN}12)${NC} 🗂️  Menu tree"
    echo ""
    echo -e "    ${RED}0)${NC}  Exit"
    echo ""
}

press_any_key() {
    echo ""
    read -r -p "$(echo -e ${CYAN}Press Enter to continue...${NC})" _
}

confirm_action() {
    local prompt="${1:-Continue?}"
    local default="${2:-N}"
    local suffix
    if [ "$default" = "Y" ]; then suffix="[Y/n]"; else suffix="[y/N]"; fi
    local ans
    read -r -p "$(echo -e ${YELLOW}${prompt} ${suffix}: ${NC})" ans
    ans=$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')
    if [ -z "$ans" ]; then
        [ "$default" = "Y" ] && return 0 || return 1
    fi
    case "$ans" in
        y|yes) return 0 ;;
        *)     return 1 ;;
    esac
}

page_output() {
    if [ -t 1 ] && command -v less >/dev/null 2>&1; then
        less -R
    else
        cat
    fi
}

# ---------- Sanity checks -----------------------------------------
_need_repo() {
    if [ ! -f "$ECO_STACK_SH" ] || [ ! -d "$ECO_SERVICES_DIR" ]; then
        echo -e "${RED}❌ ecosystem-stack/ scripts not found under: $PROJECT_ROOT${NC}"
        echo -e "${DIM}   Run leco-cli.sh from the local-ecosystem repository root.${NC}"
        return 1
    fi
    return 0
}

_need_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo -e "${RED}❌ docker CLI not found in PATH${NC}"
        return 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Docker daemon is not reachable. Start Docker Desktop first.${NC}"
        return 1
    fi
    return 0
}

_have_leco_app() { command -v leco-app >/dev/null 2>&1; }

_warn_no_leco_app() {
    echo -e "${YELLOW}⚠️  'leco-app' CLI not in PATH.${NC}"
    echo -e "${DIM}   Install with: pip install -e tools/deploy-cli${NC}"
    echo -e "${DIM}   Or use the dashboard UI (Hosted apps tab) at http://localhost:${DASHBOARD_HOST_PORT}/${NC}"
}

# ---------- Thin wrappers around existing scripts -----------------
_eco() {
    # _eco <action> [service]
    _need_repo || return 1
    bash "$ECO_STACK_SH" "$@"
}

_svc() {
    # _svc <service> <action> [args...]
    _need_repo || return 1
    local svc="$1"; shift
    local script="$ECO_SERVICES_DIR/${svc}.sh"
    if [ ! -f "$script" ]; then
        echo -e "${RED}❌ Unknown service: $svc${NC}"
        echo -e "${DIM}   Known: $(get_services_list)${NC}"
        return 1
    fi
    bash "$script" "$@"
}

get_services_list() {
    if [ -d "$ECO_SERVICES_DIR" ]; then
        for f in "$ECO_SERVICES_DIR"/*.sh; do
            basename "$f" .sh
        done | tr '\n' ' '
    fi
}

# ---------- Status snapshot ---------------------------------------
status_snapshot() {
    show_header
    echo -e "${BLUE}═══ 📊 STATUS SNAPSHOT ═══${NC}"
    echo ""

    echo -e "${WHITE}Docker${NC}"
    if command -v docker >/dev/null 2>&1; then
        if docker info >/dev/null 2>&1; then
            echo -e "  ${GREEN}✔${NC} daemon reachable"
            local ver
            ver=$(docker compose version --short 2>/dev/null || echo "n/a")
            echo -e "  ${DIM}compose plugin: ${ver}${NC}"
        else
            echo -e "  ${RED}✖${NC} daemon not reachable"
        fi
    else
        echo -e "  ${RED}✖${NC} docker CLI missing"
    fi
    echo ""

    echo -e "${WHITE}Network (lh-network)${NC}"
    if docker network inspect lh-network >/dev/null 2>&1; then
        echo -e "  ${GREEN}✔${NC} lh-network exists"
    else
        echo -e "  ${YELLOW}⚠${NC}  lh-network missing — run: $0 repair"
    fi
    echo ""

    echo -e "${WHITE}Core services (ecosystem-stack)${NC}"
    local svc name
    for svc in $SERVICES_ORDER; do
        if [ -f "$ECO_SERVICES_DIR/${svc}.sh" ]; then
            # Best-effort container name lookup. Most service scripts define `NAME=` directly.
            name=$(grep -m1 -E '^NAME=' "$ECO_SERVICES_DIR/${svc}.sh" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
            if [ -n "$name" ]; then
                local st
                # docker inspect on a missing container prints a blank line + exits 1, so
                # capture stderr separately and normalize empty stdout to "absent".
                st=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null | tr -d '\n')
                [ -z "$st" ] && st="absent"
                case "$st" in
                    running) echo -e "  ${GREEN}✔${NC} $svc  ${DIM}($name: running)${NC}" ;;
                    paused)  echo -e "  ${YELLOW}⏸${NC}  $svc  ${DIM}($name: paused)${NC}" ;;
                    exited)  echo -e "  ${RED}✖${NC} $svc  ${DIM}($name: exited)${NC}" ;;
                    absent)  echo -e "  ${DIM}○ $svc  ($name: not created)${NC}" ;;
                    *)       echo -e "  ${DIM}? $svc  ($name: $st)${NC}" ;;
                esac
            else
                # Compose-driven services (cloudflare-local, infra) don't have one NAME.
                echo -e "  ${DIM}↳ $svc  (compose-driven; see: $0 stack status $svc)${NC}"
            fi
        fi
    done
    echo ""

    echo -e "${WHITE}Traefik file provider${NC}"
    [ -f "$CORE_TRAEFIK_DYNAMIC" ] \
        && echo -e "  ${GREEN}✔${NC} traefik/dynamic.yml (canonical) present" \
        || echo -e "  ${RED}✖${NC} traefik/dynamic.yml missing"
    [ -f "$HOSTING_TRAEFIK_DIR/01-stack-core.yml" ] \
        && echo -e "  ${GREEN}✔${NC} hosting/traefik/01-stack-core.yml copy present" \
        || echo -e "  ${YELLOW}⚠${NC}  hosting/traefik/01-stack-core.yml missing — run: $0 traefik heal"
    if [ -f "$HOSTING_TRAEFIK_DYNAMIC" ]; then
        echo -e "  ${GREEN}✔${NC} hosting/traefik/dynamic.yml exists"
    else
        echo -e "  ${YELLOW}⚠${NC}  hosting/traefik/dynamic.yml missing (auto-created on traefik start)"
    fi
    echo ""

    echo -e "${WHITE}Hosted apps (from $REGISTRY_FILE)${NC}"
    if [ ! -f "$REGISTRY_FILE" ]; then
        echo -e "  ${DIM}registry not present yet — register your first app via the dashboard.${NC}"
    else
        _registry_dump_table
    fi
    echo ""
}

_python_with_yaml() {
    # Return path to a python3 interpreter that can `import yaml`, or empty.
    local py
    # Try the interpreter behind leco-app first (PyYAML is a hard dep there).
    if command -v leco-app >/dev/null 2>&1; then
        py=$(head -1 "$(command -v leco-app)" 2>/dev/null | sed 's|^#!||' | awk '{print $1}')
        if [ -x "$py" ] && "$py" -c "import yaml" >/dev/null 2>&1; then
            printf '%s' "$py"; return 0
        fi
    fi
    for py in python3 /usr/bin/python3 /opt/homebrew/bin/python3; do
        if command -v "$py" >/dev/null 2>&1 && "$py" -c "import yaml" >/dev/null 2>&1; then
            printf '%s' "$(command -v "$py")"; return 0
        fi
    done
    return 1
}

_registry_dump_table() {
    [ -f "$REGISTRY_FILE" ] || return 0
    local py
    if py=$(_python_with_yaml); then
        "$py" - "$REGISTRY_FILE" <<'PY'
import sys, yaml
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
except Exception as e:
    print(f"  (could not read registry: {e})")
    sys.exit(0)
apps = data.get("apps") or []
if not apps:
    print("  (no apps registered)")
    sys.exit(0)
print(f"  {'SLUG':<20} {'LABEL':<32} MANIFEST")
print(f"  {'----':<20} {'-----':<32} --------")
for a in apps:
    slug = str(a.get("id") or "")
    label = str(a.get("label") or slug)
    man = str(a.get("manifest") or "")
    print(f"  {slug[:20]:<20} {label[:32]:<32} {man}")
PY
        return 0
    fi
    # awk fallback for the simple registry shape (apps: \n - id:/label:/manifest:)
    printf "  %-20s %-32s %s\n" "SLUG" "LABEL" "MANIFEST"
    printf "  %-20s %-32s %s\n" "----" "-----" "--------"
    awk '
        function trim(s) { sub(/^[[:space:]]+/,"",s); sub(/[[:space:]]+$/,"",s); gsub(/^["\'\'']|["\'\'']$/,"",s); return s }
        /^[[:space:]]*-[[:space:]]*id:/ {
            if (id != "") { printf "  %-20s %-32s %s\n", id, label, manifest; id=""; label=""; manifest="" }
            sub(/^[^:]*:/,""); id=trim($0); next
        }
        /^[[:space:]]+label:/   { sub(/^[^:]*:/,""); label=trim($0); next }
        /^[[:space:]]+manifest:/{ sub(/^[^:]*:/,""); manifest=trim($0); next }
        END { if (id != "") printf "  %-20s %-32s %s\n", id, label, manifest }
    ' "$REGISTRY_FILE"
}

# ---------- Foundation installer ----------------------------------
foundation_install() {
    _need_repo || return 1
    if [ ! -f "$ECO_INSTALL_SH" ]; then
        echo -e "${RED}❌ install-foundation.sh not found at: $ECO_INSTALL_SH${NC}"
        return 1
    fi
    echo -e "${CYAN}Running foundation installer…${NC}"
    bash "$ECO_INSTALL_SH"
    return $?
}

# ---------- Generic stack action ----------------------------------
stack_action() {
    # stack_action <action> [service]
    _need_repo || return 1
    local action="$1"; shift || true
    local svc="${1:-}"
    if [ -z "$action" ]; then
        echo -e "${RED}Usage: $0 stack <start|stop|restart|deploy|pause|unpause|status|logs|remove|reset|repair-network|menu> [service]${NC}"
        return 1
    fi
    case "$action" in
        menu)
            bash "$ECO_STACK_SH" menu
            ;;
        repair-network|repair)
            bash "$ECO_STACK_SH" repair-network
            ;;
        ollama-pull-models|pull-models)
            bash "$ECO_STACK_SH" ollama-pull-models
            ;;
        heal)
            # `stack heal traefik` mirrors ecosystem-stack/services/traefik.sh heal.
            if [ -z "$svc" ]; then svc="traefik"; fi
            _svc "$svc" heal
            ;;
        *)
            if [ -n "$svc" ]; then
                bash "$ECO_STACK_SH" "$action" "$svc"
            else
                bash "$ECO_STACK_SH" "$action"
            fi
            ;;
    esac
}

menu_stack_one_service() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🏗️  ECOSYSTEM SERVICES ═══${NC}"
        echo ""
        echo -e "  Known services:"
        local i=1 svc
        for svc in $SERVICES_ORDER; do
            if [ -f "$ECO_SERVICES_DIR/${svc}.sh" ]; then
                echo -e "    ${GREEN}${i})${NC} $svc"
                i=$((i+1))
            fi
        done
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Pick a service number${NC} [0-${i}]: )" pick
        if [ -z "$pick" ] || [ "$pick" = "0" ]; then return 0; fi
        # Resolve pick → service name in same order.
        local n=1 chosen=""
        for svc in $SERVICES_ORDER; do
            if [ -f "$ECO_SERVICES_DIR/${svc}.sh" ]; then
                if [ "$n" = "$pick" ]; then chosen="$svc"; break; fi
                n=$((n+1))
            fi
        done
        if [ -z "$chosen" ]; then
            echo -e "${RED}Invalid selection.${NC}"; press_any_key; continue
        fi
        menu_service_actions "$chosen"
    done
}

menu_service_actions() {
    local svc="$1"
    while true; do
        show_header
        echo -e "${BLUE}═══ 🛠️  SERVICE: ${svc} ═══${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} start"
        echo -e "    ${GREEN}2)${NC} stop"
        echo -e "    ${GREEN}3)${NC} restart"
        echo -e "    ${GREEN}4)${NC} pause"
        echo -e "    ${GREEN}5)${NC} unpause"
        echo -e "    ${GREEN}6)${NC} status"
        echo -e "    ${GREEN}7)${NC} logs (Ctrl-C to stop)"
        echo -e "    ${GREEN}8)${NC} deploy / recreate (if defined)"
        echo -e "    ${YELLOW}9)${NC} remove (destructive)"
        echo -e "    ${YELLOW}10)${NC} reset  (destructive: container + volumes)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose action${NC} [0-10]: )" ch
        case "$ch" in
            1)  _eco start "$svc"; press_any_key ;;
            2)  _eco stop "$svc"; press_any_key ;;
            3)  _eco restart "$svc"; press_any_key ;;
            4)  _eco pause "$svc"; press_any_key ;;
            5)  _eco unpause "$svc"; press_any_key ;;
            6)  _eco status "$svc"; press_any_key ;;
            7)  _eco logs "$svc" ;;
            8)  _eco deploy "$svc"; press_any_key ;;
            9)  confirm_action "Remove '$svc' container?" "N" && _eco remove "$svc"; press_any_key ;;
            10) confirm_action "Reset '$svc' (container + volumes)?" "N" && _eco reset "$svc"; press_any_key ;;
            0)  return 0 ;;
            *)  echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

menu_stack_bulk() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🏗️  BULK ECOSYSTEM ═══${NC}"
        echo -e "${DIM}Bulk skips dashboard on stop / pause / remove so the request can finish.${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} start all (in dependency order)"
        echo -e "    ${GREEN}2)${NC} stop all (preserving core)"
        echo -e "    ${GREEN}3)${NC} restart all"
        echo -e "    ${GREEN}4)${NC} deploy all (bulk_ecosystem)"
        echo -e "    ${GREEN}5)${NC} pause all"
        echo -e "    ${GREEN}6)${NC} unpause all"
        echo -e "    ${GREEN}7)${NC} status all"
        echo -e "    ${YELLOW}8)${NC} remove all (destructive)"
        echo -e "    ${YELLOW}9)${NC} reset all  (destructive: data volumes too)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose action${NC} [0-9]: )" ch
        case "$ch" in
            1) _eco start; press_any_key ;;
            2) _eco stop;  press_any_key ;;
            3) _eco restart; press_any_key ;;
            4) _eco deploy; press_any_key ;;
            5) _eco pause; press_any_key ;;
            6) _eco unpause; press_any_key ;;
            7) _eco status; press_any_key ;;
            8) confirm_action "Remove ALL managed containers?" "N" && _eco remove; press_any_key ;;
            9) confirm_action "Reset ALL services + delete data volumes?" "N" && _eco reset; press_any_key ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

menu_stack() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🏗️  CORE STACK ═══${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Manage one service"
        echo -e "    ${GREEN}2)${NC} Bulk actions across all services"
        echo -e "    ${GREEN}3)${NC} Repair network links (lh-network)"
        echo -e "    ${GREEN}4)${NC} Service status summary"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-4]: )" ch
        case "$ch" in
            1) menu_stack_one_service ;;
            2) menu_stack_bulk ;;
            3) _eco repair-network; press_any_key ;;
            4) _eco status; press_any_key ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Dashboard sub-menu ------------------------------------
menu_dashboard() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🖥️  LECO DASHBOARD ═══${NC}"
        echo -e "${DIM}Container: service-dashboard · host port: ${DASHBOARD_HOST_PORT}${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Deploy (rebuild image + restart container)"
        echo -e "    ${GREEN}2)${NC} Quick restart (no rebuild, picks up code changes)"
        echo -e "    ${GREEN}3)${NC} Start"
        echo -e "    ${GREEN}4)${NC} Stop"
        echo -e "    ${GREEN}5)${NC} Status"
        echo -e "    ${GREEN}6)${NC} Logs (Ctrl-C to stop)"
        echo -e "    ${GREEN}7)${NC} Open http://localhost:${DASHBOARD_HOST_PORT}"
        echo -e "    ${YELLOW}8)${NC} Remove container (data on disk safe)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-8]: )" ch
        case "$ch" in
            1) _svc dashboard deploy; press_any_key ;;
            2) _svc dashboard quick; press_any_key ;;
            3) _svc dashboard start; press_any_key ;;
            4) _svc dashboard stop; press_any_key ;;
            5) _svc dashboard status; press_any_key ;;
            6) _svc dashboard logs ;;
            7) open_url "http://localhost:${DASHBOARD_HOST_PORT}" ;;
            8) confirm_action "Remove service-dashboard?" "N" && _svc dashboard remove; press_any_key ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Traefik sub-menu --------------------------------------
menu_traefik() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🌐 TRAEFIK & ROUTING ═══${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Heal hosting files + restart Traefik (if running)"
        echo -e "    ${GREEN}2)${NC} Ensure hosting/traefik/* files only"
        echo -e "    ${GREEN}3)${NC} Start / restart Traefik service"
        echo -e "    ${GREEN}4)${NC} Status"
        echo -e "    ${GREEN}5)${NC} Logs (Ctrl-C to stop)"
        echo -e "    ${GREEN}6)${NC} Open Traefik dashboard (https://traefik.lh)"
        echo -e "    ${GREEN}7)${NC} Show core dynamic.yml (read-only)"
        echo -e "    ${GREEN}8)${NC} Show hosting merge dynamic.yml"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-8]: )" ch
        case "$ch" in
            1) _svc traefik heal; press_any_key ;;
            2) _svc traefik ensure-hosting-files; press_any_key ;;
            3) _svc traefik restart; press_any_key ;;
            4) _svc traefik status; press_any_key ;;
            5) _svc traefik logs ;;
            6) open_url "https://traefik.lh" ;;
            7)
                if [ -f "$CORE_TRAEFIK_DYNAMIC" ]; then
                    cat "$CORE_TRAEFIK_DYNAMIC" | page_output
                else
                    echo -e "${RED}Missing: $CORE_TRAEFIK_DYNAMIC${NC}"
                    press_any_key
                fi
                ;;
            8)
                if [ -f "$HOSTING_TRAEFIK_DYNAMIC" ]; then
                    cat "$HOSTING_TRAEFIK_DYNAMIC" | page_output
                else
                    echo -e "${DIM}Empty (no hosted apps merged yet): $HOSTING_TRAEFIK_DYNAMIC${NC}"
                    press_any_key
                fi
                ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Cloudflare-local sub-menu -----------------------------
menu_cf_local() {
    while true; do
        show_header
        echo -e "${BLUE}═══ ☁️  CLOUDFLARE-LOCAL ═══${NC}"
        echo -e "${DIM}KV → http://kv.lh   R2 → http://r2.lh   D1 → http://d1.lh   Workers → http://workers.lh${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Start (compose up -d --build)"
        echo -e "    ${GREEN}2)${NC} Stop"
        echo -e "    ${GREEN}3)${NC} Restart"
        echo -e "    ${GREEN}4)${NC} Status"
        echo -e "    ${GREEN}5)${NC} Logs (Ctrl-C to stop)"
        echo -e "    ${GREEN}6)${NC} Recreate (pass service name when prompted, or blank for all)"
        echo -e "    ${GREEN}7)${NC} Backup D1 databases"
        echo -e "    ${GREEN}8)${NC} Open http://kv.lh"
        echo -e "    ${GREEN}9)${NC} Open http://r2.lh"
        echo -e "    ${GREEN}10)${NC} Open http://d1.lh"
        echo -e "    ${YELLOW}11)${NC} Remove containers (keep volumes)"
        echo -e "    ${YELLOW}12)${NC} Reset (down -v, deletes adapter volumes)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-12]: )" ch
        case "$ch" in
            1)  _svc cloudflare-local start; press_any_key ;;
            2)  _svc cloudflare-local stop; press_any_key ;;
            3)  _svc cloudflare-local restart; press_any_key ;;
            4)  _svc cloudflare-local status; press_any_key ;;
            5)  _svc cloudflare-local logs ;;
            6)
                read -r -p "$(echo -e ${CYAN}Compose service name${NC} [blank=all]: )" rs
                _svc cloudflare-local recreate "$rs"; press_any_key ;;
            7)  _svc cloudflare-local backup; press_any_key ;;
            8)  open_url "http://kv.lh" ;;
            9)  open_url "http://r2.lh" ;;
            10) open_url "http://d1.lh" ;;
            11) confirm_action "Remove cloudflare-local containers?" "N" && _svc cloudflare-local remove; press_any_key ;;
            12) confirm_action "Reset cloudflare-local (delete adapter volumes)?" "N" && _svc cloudflare-local reset; press_any_key ;;
            0)  return 0 ;;
            *)  echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Ollama sub-menu ---------------------------------------
menu_ollama() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🦙 OLLAMA ═══${NC}"
        echo -e "${DIM}Pinned models: ecosystem-stack/config/ollama-pinned-models.txt${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Pull pinned models into running ollama"
        echo -e "    ${GREEN}2)${NC} Status"
        echo -e "    ${GREEN}3)${NC} List installed models (docker exec)"
        echo -e "    ${GREEN}4)${NC} Logs (Ctrl-C to stop)"
        echo -e "    ${GREEN}5)${NC} Restart"
        echo -e "    ${GREEN}6)${NC} Open https://ai.lh (Open WebUI)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-6]: )" ch
        case "$ch" in
            1) _eco ollama-pull-models; press_any_key ;;
            2) _svc ollama status; press_any_key ;;
            3) docker exec ollama ollama list 2>&1 | page_output ;;
            4) _svc ollama logs ;;
            5) _svc ollama restart; press_any_key ;;
            6) open_url "https://ai.lh" ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Hosted apps (leco-app) --------------------------------
_registry_slugs() {
    [ -f "$REGISTRY_FILE" ] || return 0
    local py
    if py=$(_python_with_yaml); then
        "$py" - "$REGISTRY_FILE" <<'PY'
import sys, yaml
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
except Exception:
    sys.exit(0)
for a in (data.get("apps") or []):
    slug = str(a.get("id") or "").strip()
    if slug:
        print(slug)
PY
        return 0
    fi
    awk '
        /^[[:space:]]*-[[:space:]]*id:/ {
            sub(/^[^:]*:/,""); gsub(/^[[:space:]]+|[[:space:]]+$/,""); gsub(/^["\'\'']|["\'\'']$/,""); print
        }
    ' "$REGISTRY_FILE"
}

_manifest_for_slug() {
    local slug="$1"
    [ -f "$REGISTRY_FILE" ] || return 1
    local py
    if py=$(_python_with_yaml); then
        "$py" - "$REGISTRY_FILE" "$slug" <<'PY'
import sys, yaml
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
except Exception:
    sys.exit(1)
want = sys.argv[2]
for a in (data.get("apps") or []):
    if str(a.get("id") or "").strip() == want:
        man = str(a.get("manifest") or "").strip()
        print(man); sys.exit(0)
sys.exit(2)
PY
        return $?
    fi
    awk -v want="$slug" '
        function trim(s) { sub(/^[[:space:]]+/,"",s); sub(/[[:space:]]+$/,"",s); gsub(/^["\'\'']|["\'\'']$/,"",s); return s }
        /^[[:space:]]*-[[:space:]]*id:/ {
            if (cur_id == want && cur_man != "") { print cur_man; exit 0 }
            sub(/^[^:]*:/,""); cur_id=trim($0); cur_man=""; next
        }
        /^[[:space:]]+manifest:/ { sub(/^[^:]*:/,""); cur_man=trim($0); next }
        END { if (cur_id == want && cur_man != "") { print cur_man; exit 0 } else exit 2 }
    ' "$REGISTRY_FILE"
}

_abs_manifest() {
    # _abs_manifest <relpath>  →  absolute path under PROJECT_ROOT or as-is when absolute.
    local rel="$1"
    case "$rel" in
        /*) printf '%s' "$rel" ;;
        *)  printf '%s' "$PROJECT_ROOT/$rel" ;;
    esac
}

apps_list() {
    if [ ! -f "$REGISTRY_FILE" ]; then
        echo -e "${DIM}Registry file not present: $REGISTRY_FILE${NC}"
        return 0
    fi
    _registry_dump_table
}

apps_run() {
    # apps_run <slug> <leco-app sub> [extra args...]
    local slug="$1"; shift
    local sub="$1"; shift || true
    if [ -z "$slug" ] || [ -z "$sub" ]; then
        echo -e "${RED}Usage: $0 apps <deploy|stop|down|logs|status|onboard|register|unregister> <slug> [args]${NC}"
        return 1
    fi
    if ! _have_leco_app; then _warn_no_leco_app; return 1; fi
    local rel
    rel=$(_manifest_for_slug "$slug") || true
    if [ -z "$rel" ]; then
        echo -e "${RED}❌ slug not found in registry: $slug${NC}"
        echo -e "${DIM}   Known slugs: $(_registry_slugs | tr '\n' ' ')${NC}"
        return 1
    fi
    local abs
    abs=$(_abs_manifest "$rel")
    if [ ! -f "$abs" ]; then
        echo -e "${RED}❌ manifest path missing on disk: $abs${NC}"
        return 1
    fi
    echo -e "${CYAN}leco-app $sub --manifest $abs $*${NC}"
    leco-app "$sub" --manifest "$abs" "$@"
    return $?
}

apps_onboard_path() {
    # apps_onboard_path <path-to-leco.app.yaml-or-dir>
    local target="$1"
    if [ -z "$target" ]; then
        echo -e "${RED}Usage: $0 apps onboard <path-to-leco.app.yaml-or-dir>${NC}"
        return 1
    fi
    if ! _have_leco_app; then _warn_no_leco_app; return 1; fi
    # leco-app accepts --cwd or --manifest; pick whichever fits the path.
    if [ -d "$target" ]; then
        leco-app onboard --cwd "$target" -E "$PROJECT_ROOT"
    elif [ -f "$target" ]; then
        leco-app onboard --manifest "$target" -E "$PROJECT_ROOT"
    else
        echo -e "${RED}❌ Not a file or directory: $target${NC}"
        return 1
    fi
}

apps_unregister_slug() {
    local slug="$1"
    if [ -z "$slug" ]; then
        echo -e "${RED}Usage: $0 apps unregister <slug>${NC}"
        return 1
    fi
    if ! _have_leco_app; then _warn_no_leco_app; return 1; fi
    leco-app ecosystem-unregister "$slug" -E "$PROJECT_ROOT"
    return $?
}

menu_apps() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 📦 HOSTED APPS (leco-app) ═══${NC}"
        echo ""
        apps_list
        echo ""
        echo -e "    ${GREEN}1)${NC} Refresh / re-list registered apps"
        echo -e "    ${GREEN}2)${NC} Per-app actions (deploy / stop / down / logs / status / register / unregister)"
        echo -e "    ${GREEN}3)${NC} Onboard a new app from path"
        echo -e "    ${GREEN}4)${NC} Print Traefik fragment for a registered app"
        echo -e "    ${GREEN}5)${NC} Open dashboard Hosted apps page"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-5]: )" ch
        case "$ch" in
            1) press_any_key ;;
            2) menu_apps_pick ;;
            3) read -r -p "Path to leco.app.yaml or its directory: " p; apps_onboard_path "$p"; press_any_key ;;
            4) read -r -p "Slug: " sl; apps_run "$sl" traefik-fragment; press_any_key ;;
            5) open_url "http://localhost:${DASHBOARD_HOST_PORT}/#/hosted-apps" ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

menu_apps_pick() {
    local slugs
    slugs=$(_registry_slugs | tr '\n' ' ')
    if [ -z "$slugs" ]; then
        echo -e "${DIM}No registered apps. Onboard one first.${NC}"
        press_any_key
        return 0
    fi
    show_header
    echo -e "${BLUE}═══ 📦 PICK APP ═══${NC}"
    echo ""
    local i=1
    for s in $slugs; do
        echo -e "    ${GREEN}${i})${NC} $s"
        i=$((i+1))
    done
    echo ""
    echo -e "    ${YELLOW}0)${NC} Back"
    echo ""
    read -r -p "$(echo -e ${CYAN}Pick a slug${NC}: )" pick
    if [ -z "$pick" ] || [ "$pick" = "0" ]; then return 0; fi
    local n=1 chosen=""
    for s in $slugs; do
        if [ "$n" = "$pick" ]; then chosen="$s"; break; fi
        n=$((n+1))
    done
    if [ -z "$chosen" ]; then
        echo -e "${RED}Invalid selection${NC}"; press_any_key; return 0
    fi
    menu_apps_actions "$chosen"
}

menu_apps_actions() {
    local slug="$1"
    while true; do
        show_header
        echo -e "${BLUE}═══ 📦 APP: ${slug} ═══${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Status (docker compose ps + health probes)"
        echo -e "    ${GREEN}2)${NC} Deploy (docker compose up -d --build)"
        echo -e "    ${GREEN}3)${NC} Stop  (docker compose stop)"
        echo -e "    ${GREEN}4)${NC} Logs  (Ctrl-C to stop)"
        echo -e "    ${GREEN}5)${NC} Down  (compose down, keep volumes)"
        echo -e "    ${GREEN}6)${NC} Offload (down -v + strip Traefik routes, files kept)"
        echo -e "    ${GREEN}7)${NC} Re-register / merge Traefik"
        echo -e "    ${GREEN}8)${NC} Provision local CF (KV/R2/D1)"
        echo -e "    ${GREEN}9)${NC} Print Traefik fragment (preview)"
        echo -e "    ${YELLOW}10)${NC} Unregister (full offboard via leco-app)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose action${NC} [0-10]: )" ch
        case "$ch" in
            1)  apps_run "$slug" status; press_any_key ;;
            2)  apps_run "$slug" deploy; press_any_key ;;
            3)  apps_run "$slug" stop; press_any_key ;;
            4)  apps_run "$slug" logs ;;
            5)  apps_run "$slug" down; press_any_key ;;
            6)  confirm_action "Offload ${slug} (down -v + Traefik strip)?" "N" && apps_run "$slug" offload -y; press_any_key ;;
            7)
                if ! _have_leco_app; then _warn_no_leco_app; press_any_key; continue; fi
                local rel abs
                rel=$(_manifest_for_slug "$slug") && abs=$(_abs_manifest "$rel")
                if [ -n "$abs" ] && [ -f "$abs" ]; then
                    leco-app ecosystem-register --manifest "$abs" -E "$PROJECT_ROOT" --merge-traefik
                else
                    echo -e "${RED}manifest not found for $slug${NC}"
                fi
                press_any_key
                ;;
            8)
                if ! _have_leco_app; then _warn_no_leco_app; press_any_key; continue; fi
                local rel abs
                rel=$(_manifest_for_slug "$slug") && abs=$(_abs_manifest "$rel")
                [ -n "$abs" ] && leco-app provision-local-cf --manifest "$abs"
                press_any_key
                ;;
            9)  apps_run "$slug" traefik-fragment; press_any_key ;;
            10) confirm_action "Unregister ${slug} (compose down + Traefik strip + registry remove)?" "N" \
                    && apps_unregister_slug "$slug"; press_any_key ;;
            0)  return 0 ;;
            *)  echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- URLs / open helpers -----------------------------------
open_url() {
    local url="$1"
    [ -n "$url" ] || return 0
    echo -e "${CYAN}Opening: ${url}${NC}"
    case "$(uname -s)" in
        Darwin) open "$url" 2>/dev/null || echo "$url" ;;
        Linux)  command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" 2>/dev/null || echo "$url" ;;
        *)      echo "$url" ;;
    esac
}

urls_print() {
    echo -e "${BLUE}═══ 🔗 SERVICE URLs ═══${NC}"
    echo ""
    printf "  %-30s %s\n" "LEco Dashboard"  "http://localhost:${DASHBOARD_HOST_PORT}"
    printf "  %-30s %s\n" "LEco via Traefik" "http://localhost.lh"
    printf "  %-30s %s\n" "Traefik dashboard" "https://traefik.lh"
    printf "  %-30s %s\n" "Open WebUI (AI)"   "https://ai.lh"
    printf "  %-30s %s\n" "n8n"               "https://n8n.lh"
    printf "  %-30s %s\n" "Ollama"            "https://ollama.lh"
    printf "  %-30s %s\n" "CF-local KV"       "http://kv.lh"
    printf "  %-30s %s\n" "CF-local R2"       "http://r2.lh"
    printf "  %-30s %s\n" "CF-local D1"       "http://d1.lh"
    printf "  %-30s %s\n" "CF-local Workers"  "http://workers.lh"
    printf "  %-30s %s\n" "MinIO console"     "http://minio-console.lh"
    printf "  %-30s %s\n" "Autoscale demo"    "http://autoscale.lh"
    echo ""
}

menu_urls() {
    while true; do
        show_header
        urls_print
        echo -e "    ${GREEN}1)${NC} Open LEco Dashboard"
        echo -e "    ${GREEN}2)${NC} Open Traefik dashboard"
        echo -e "    ${GREEN}3)${NC} Open Open WebUI"
        echo -e "    ${GREEN}4)${NC} Open n8n"
        echo -e "    ${GREEN}5)${NC} Open kv.lh"
        echo -e "    ${GREEN}6)${NC} Open r2.lh"
        echo -e "    ${GREEN}7)${NC} Open d1.lh"
        echo -e "    ${GREEN}8)${NC} Print URLs only (skip open)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose${NC} [0-8]: )" ch
        case "$ch" in
            1) open_url "http://localhost:${DASHBOARD_HOST_PORT}" ;;
            2) open_url "https://traefik.lh" ;;
            3) open_url "https://ai.lh" ;;
            4) open_url "https://n8n.lh" ;;
            5) open_url "http://kv.lh" ;;
            6) open_url "http://r2.lh" ;;
            7) open_url "http://d1.lh" ;;
            8) press_any_key ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Diagnostics -------------------------------------------
diagnostics_run() {
    show_header
    echo -e "${BLUE}═══ 🩺 DIAGNOSTICS ═══${NC}"
    echo ""

    echo -e "${WHITE}1. docker info${NC}"
    if docker info >/dev/null 2>&1; then
        echo -e "  ${GREEN}OK${NC}"
    else
        echo -e "  ${RED}docker daemon unreachable${NC}"
    fi
    echo ""

    echo -e "${WHITE}2. *.lh DNS resolution${NC}"
    for h in localhost.lh kv.lh r2.lh d1.lh; do
        local ip
        ip=$(getent hosts "$h" 2>/dev/null | awk '{print $1}' | head -n1)
        if [ -z "$ip" ]; then
            ip=$(dscacheutil -q host -a name "$h" 2>/dev/null | awk '/ip_address/{print $2; exit}')
        fi
        if [ -n "$ip" ]; then
            echo -e "  ${GREEN}✔${NC} $h → $ip"
        else
            echo -e "  ${YELLOW}⚠${NC}  $h does not resolve (check dnsmasq + /etc/resolver/lh)"
        fi
    done
    echo ""

    echo -e "${WHITE}3. lh-network${NC}"
    if docker network inspect lh-network >/dev/null 2>&1; then
        echo -e "  ${GREEN}✔${NC} lh-network exists"
    else
        echo -e "  ${RED}✖${NC} lh-network missing — run: $0 repair"
    fi
    echo ""

    echo -e "${WHITE}4. mkcert certs${NC}"
    if [ -f "$PROJECT_ROOT/certs/wildcard.lh.pem" ] && [ -f "$PROJECT_ROOT/certs/wildcard.lh-key.pem" ]; then
        echo -e "  ${GREEN}✔${NC} certs/wildcard.lh.pem + key present"
    else
        echo -e "  ${YELLOW}⚠${NC}  certs missing — see foundation installer (option 2)"
    fi
    echo ""

    echo -e "${WHITE}5. Traefik dynamic files${NC}"
    [ -f "$CORE_TRAEFIK_DYNAMIC" ] && echo -e "  ${GREEN}✔${NC} traefik/dynamic.yml" \
        || echo -e "  ${RED}✖${NC} traefik/dynamic.yml missing"
    [ -f "$HOSTING_TRAEFIK_DIR/01-stack-core.yml" ] \
        && echo -e "  ${GREEN}✔${NC} hosting/traefik/01-stack-core.yml" \
        || echo -e "  ${YELLOW}⚠${NC}  hosting/traefik/01-stack-core.yml missing — run: $0 traefik heal"
    echo ""

    echo -e "${WHITE}6. leco-app CLI${NC}"
    if _have_leco_app; then
        echo -e "  ${GREEN}✔${NC} $(command -v leco-app)"
    else
        echo -e "  ${YELLOW}⚠${NC}  leco-app not in PATH — pip install -e tools/deploy-cli"
    fi
}

repair_run() {
    show_header
    echo -e "${BLUE}═══ 🩹 REPAIR ═══${NC}"
    echo ""
    echo -e "${CYAN}1) Repairing network links…${NC}"
    _eco repair-network
    echo ""
    echo -e "${CYAN}2) Healing Traefik hosting files (restarts Traefik if running)…${NC}"
    _svc traefik heal
    echo ""
    echo -e "${GREEN}Repair pass complete.${NC}"
}

menu_diagnostics() {
    while true; do
        show_header
        echo -e "${BLUE}═══ 🩺 DIAGNOSTICS & REPAIR ═══${NC}"
        echo ""
        echo -e "    ${GREEN}1)${NC} Run diagnostics report"
        echo -e "    ${GREEN}2)${NC} Repair network + heal Traefik"
        echo -e "    ${GREEN}3)${NC} Repair lh-network only"
        echo -e "    ${GREEN}4)${NC} Heal Traefik only"
        echo -e "    ${GREEN}5)${NC} Recreate hosting/traefik files (no restart)"
        echo ""
        echo -e "    ${YELLOW}0)${NC} Back"
        echo ""
        read -r -p "$(echo -e ${CYAN}Choose option${NC} [0-5]: )" ch
        case "$ch" in
            1) diagnostics_run; press_any_key ;;
            2) repair_run; press_any_key ;;
            3) _eco repair-network; press_any_key ;;
            4) _svc traefik heal; press_any_key ;;
            5) _svc traefik ensure-hosting-files; press_any_key ;;
            0) return 0 ;;
            *) echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Reference ---------------------------------------------
reference_help_detailed() {
    show_header
    cat <<'TXT' | page_output
LEco DevOps — leco-cli.sh quick reference
=========================================

Run without arguments for the interactive menu. Direct commands mirror the menu.

Status / health
  leco-cli.sh status                      Snapshot: docker, network, services, hosted apps
  leco-cli.sh diagnose | doctor           Detailed health (DNS, certs, Traefik files, leco-app)
  leco-cli.sh repair                      Repair network + heal Traefik

Foundation
  leco-cli.sh install                     Run ecosystem-stack/install-foundation.sh

Ecosystem stack (wraps ecosystem-stack/ecosystem-stack.sh)
  leco-cli.sh stack start [svc]
  leco-cli.sh stack stop  [svc]
  leco-cli.sh stack restart [svc]
  leco-cli.sh stack deploy [svc]          Bulk deploy if svc omitted (bulk_ecosystem)
  leco-cli.sh stack pause|unpause [svc]
  leco-cli.sh stack status [svc]
  leco-cli.sh stack logs <svc>            Follow logs for one service
  leco-cli.sh stack remove [svc]          (destructive)
  leco-cli.sh stack reset  [svc]          (destructive: containers + volumes)
  leco-cli.sh stack repair-network
  leco-cli.sh stack ollama-pull-models
  leco-cli.sh stack menu                  Original ecosystem-stack interactive menu

Per-service shortcuts (wrap ecosystem-stack/services/<name>.sh)
  leco-cli.sh dashboard <deploy|quick|start|stop|status|logs|remove>
  leco-cli.sh traefik   <heal|ensure-files|restart|status|logs>
  leco-cli.sh cf        <start|stop|restart|status|logs|recreate|backup|remove|reset>
  leco-cli.sh ollama    <pull|status|list|logs|restart>

Hosted apps (wraps leco-app; manifests resolved via config/leco-registry.yaml)
  leco-cli.sh apps list
  leco-cli.sh apps status   <slug>
  leco-cli.sh apps deploy   <slug>
  leco-cli.sh apps stop     <slug>
  leco-cli.sh apps down     <slug>
  leco-cli.sh apps logs     <slug>
  leco-cli.sh apps offload  <slug>        (down -v + strip Traefik routes; files kept)
  leco-cli.sh apps register <slug>        Re-merge Traefik for an already-registered app
  leco-cli.sh apps unregister <slug>      Full offboard (compose down + Traefik strip + registry)
  leco-cli.sh apps onboard <path>         Path to leco.app.yaml or its dir → onboard new app
  leco-cli.sh apps provision <slug>       Provision local KV/R2/D1 from wrangler.toml
  leco-cli.sh apps fragment <slug>        Print Traefik fragment for the app

Helpers
  leco-cli.sh urls                        Print common *.lh URLs
  leco-cli.sh open <key>                  open dashboard|traefik|webui|n8n|kv|r2|d1
  leco-cli.sh menu                        Interactive menu (same as no args)
  leco-cli.sh tree                        Print menu tree
  leco-cli.sh help | --help | -h          This help

Tips
- Most subcommands shell into the existing scripts under ecosystem-stack/services/*.sh
  so behavior is identical to running those scripts directly.
- Hosted apps require `leco-app` on PATH: pip install -e tools/deploy-cli
- The repo path is auto-detected from this script's location.
TXT
}

reference_menu_tree() {
    show_header
    cat <<'TXT' | page_output
LEco CLI — menu tree
====================
1) Status (stack + hosted apps overview)
2) Foundation install
3) Manage ecosystem services
   ├─ Manage one service
   │   └─ start | stop | restart | pause | unpause | status | logs | deploy | remove | reset
   ├─ Bulk actions
   │   └─ start all | stop all | restart all | deploy all | pause/unpause | status | remove/reset
   ├─ Repair network links
   └─ Service status summary
4) LEco Dashboard
   └─ deploy | quick restart | start | stop | status | logs | open | remove
5) Traefik & routing
   └─ heal | ensure-hosting-files | restart | status | logs | open dashboard | show dynamic files
6) Cloudflare-local
   └─ start/stop/restart/status/logs | recreate | backup D1 | open kv/r2/d1 | remove/reset
7) Ollama
   └─ pull pinned | status | list models | logs | restart | open ai.lh
8) Hosted apps (leco-app)
   ├─ List registered apps
   ├─ Per-app actions
   │   └─ status | deploy | stop | logs | down | offload | re-register | provision | fragment | unregister
   ├─ Onboard a new app from path
   ├─ Print Traefik fragment for an app
   └─ Open Hosted apps page in dashboard
9) Open service URLs
10) Diagnostics / repair
   └─ report | repair (network + Traefik) | repair network only | heal Traefik only
11) Help (detailed)
12) Menu tree
0) Exit
TXT
}

# ---------- Main interactive loop ---------------------------------
main() {
    activity_init
    activity_log "session_start" "info" "$(uname -srm)"
    while true; do
        show_header
        show_menu
        read -r -p "$(echo -e ${CYAN}Select option${NC} [0-12]: )" choice
        case "$choice" in
            0)  activity_log "session_end" "info"; exit 0 ;;
            1)  activity_log "status" "info"; status_snapshot; press_any_key ;;
            2)  activity_log "install" "info"; foundation_install; press_any_key ;;
            3)  activity_log "stack" "info"; menu_stack ;;
            4)  activity_log "dashboard" "info"; menu_dashboard ;;
            5)  activity_log "traefik" "info"; menu_traefik ;;
            6)  activity_log "cf-local" "info"; menu_cf_local ;;
            7)  activity_log "ollama" "info"; menu_ollama ;;
            8)  activity_log "apps" "info"; menu_apps ;;
            9)  activity_log "urls" "info"; menu_urls ;;
            10) activity_log "diagnose" "info"; menu_diagnostics ;;
            11) reference_help_detailed ;;
            12) reference_menu_tree ;;
            *)  echo -e "${RED}Invalid option${NC}"; press_any_key ;;
        esac
    done
}

# ---------- Direct command dispatcher -----------------------------
activity_init

if [ -n "${1:-}" ]; then
    cmd="$1"; shift || true
    case "$cmd" in
        status|st)
            status_snapshot
            ;;
        install|setup|bootstrap)
            foundation_install
            ;;
        repair|fix)
            repair_run
            ;;
        diagnose|doctor|diag)
            diagnostics_run
            ;;
        stack|svc|services)
            stack_action "$@"
            ;;
        dashboard|ui)
            sub="${1:-status}"; shift || true
            case "$sub" in
                deploy)  _svc dashboard deploy ;;
                quick)   _svc dashboard quick ;;
                start)   _svc dashboard start ;;
                stop)    _svc dashboard stop ;;
                restart) _svc dashboard restart ;;
                status)  _svc dashboard status ;;
                logs)    _svc dashboard logs ;;
                open)    open_url "http://localhost:${DASHBOARD_HOST_PORT}" ;;
                remove)  confirm_action "Remove service-dashboard?" "N" && _svc dashboard remove ;;
                *)
                    echo "Usage: $0 dashboard <deploy|quick|start|stop|restart|status|logs|open|remove>"
                    exit 1
                    ;;
            esac
            ;;
        traefik)
            sub="${1:-status}"; shift || true
            case "$sub" in
                heal)           _svc traefik heal ;;
                ensure-files)   _svc traefik ensure-hosting-files ;;
                restart)        _svc traefik restart ;;
                start)          _svc traefik start ;;
                stop)           _svc traefik stop ;;
                status)         _svc traefik status ;;
                logs)           _svc traefik logs ;;
                open)           open_url "https://traefik.lh" ;;
                show-core)      [ -f "$CORE_TRAEFIK_DYNAMIC" ] && cat "$CORE_TRAEFIK_DYNAMIC" || echo "missing $CORE_TRAEFIK_DYNAMIC" ;;
                show-hosting)   [ -f "$HOSTING_TRAEFIK_DYNAMIC" ] && cat "$HOSTING_TRAEFIK_DYNAMIC" || echo "(empty / no hosted apps merged)" ;;
                *)
                    echo "Usage: $0 traefik <heal|ensure-files|restart|start|stop|status|logs|open|show-core|show-hosting>"
                    exit 1
                    ;;
            esac
            ;;
        cf|cloudflare|cf-local|cflocal)
            sub="${1:-status}"; shift || true
            case "$sub" in
                start)    _svc cloudflare-local start ;;
                stop)     _svc cloudflare-local stop ;;
                restart)  _svc cloudflare-local restart ;;
                status)   _svc cloudflare-local status ;;
                logs)     _svc cloudflare-local logs ;;
                recreate) _svc cloudflare-local recreate "${1:-}" ;;
                backup)   _svc cloudflare-local backup ;;
                remove)   _svc cloudflare-local remove ;;
                reset)    _svc cloudflare-local reset ;;
                *)
                    echo "Usage: $0 cf <start|stop|restart|status|logs|recreate [svc]|backup|remove|reset>"
                    exit 1
                    ;;
            esac
            ;;
        ollama)
            sub="${1:-status}"; shift || true
            case "$sub" in
                pull|pull-models) _eco ollama-pull-models ;;
                status)           _svc ollama status ;;
                list)             docker exec ollama ollama list ;;
                logs)             _svc ollama logs ;;
                restart)          _svc ollama restart ;;
                *)
                    echo "Usage: $0 ollama <pull|status|list|logs|restart>"
                    exit 1
                    ;;
            esac
            ;;
        apps|app)
            sub="${1:-list}"; shift || true
            case "$sub" in
                list|ls)        apps_list ;;
                status)         apps_run "${1:-}" status ;;
                deploy|up)      apps_run "${1:-}" deploy ;;
                stop)           apps_run "${1:-}" stop ;;
                down)           apps_run "${1:-}" down ;;
                logs)           apps_run "${1:-}" logs ;;
                offload|stage)  apps_run "${1:-}" offload -y ;;
                fragment|frag)  apps_run "${1:-}" traefik-fragment ;;
                provision)
                    slug="${1:-}"; shift || true
                    if ! _have_leco_app; then _warn_no_leco_app; exit 1; fi
                    rel=$(_manifest_for_slug "$slug") && abs=$(_abs_manifest "$rel")
                    [ -n "$abs" ] && [ -f "$abs" ] && leco-app provision-local-cf --manifest "$abs" "$@"
                    ;;
                register|re-register)
                    slug="${1:-}"; shift || true
                    if ! _have_leco_app; then _warn_no_leco_app; exit 1; fi
                    rel=$(_manifest_for_slug "$slug") && abs=$(_abs_manifest "$rel")
                    [ -n "$abs" ] && [ -f "$abs" ] && leco-app ecosystem-register --manifest "$abs" -E "$PROJECT_ROOT" --merge-traefik "$@"
                    ;;
                unregister|offboard)
                    apps_unregister_slug "${1:-}"
                    ;;
                onboard|new)
                    apps_onboard_path "${1:-}"
                    ;;
                *)
                    echo "Usage: $0 apps <list|status|deploy|stop|down|logs|offload|fragment|provision|register|unregister|onboard> [slug|path]"
                    exit 1
                    ;;
            esac
            ;;
        urls)
            urls_print
            ;;
        open)
            sub="${1:-dashboard}"
            case "$sub" in
                dashboard|ui)        open_url "http://localhost:${DASHBOARD_HOST_PORT}" ;;
                traefik)             open_url "https://traefik.lh" ;;
                webui|ai|open-webui) open_url "https://ai.lh" ;;
                n8n)                 open_url "https://n8n.lh" ;;
                ollama)              open_url "https://ollama.lh" ;;
                kv)                  open_url "http://kv.lh" ;;
                r2)                  open_url "http://r2.lh" ;;
                d1)                  open_url "http://d1.lh" ;;
                workers)             open_url "http://workers.lh" ;;
                *)
                    echo "Usage: $0 open <dashboard|traefik|webui|n8n|ollama|kv|r2|d1|workers>"
                    exit 1
                    ;;
            esac
            ;;
        menu|interactive)
            main
            ;;
        tree)
            reference_menu_tree
            ;;
        help|--help|-h)
            reference_help_detailed
            ;;
        version|--version|-v)
            echo "leco-cli.sh (LEco DevOps Open Project) — local manager"
            if _have_leco_app; then
                leco-app version 2>/dev/null || true
            fi
            ;;
        *)
            echo "Unknown command: $cmd"
            echo "Run '$0 help' for usage."
            exit 1
            ;;
    esac
else
    main
fi
