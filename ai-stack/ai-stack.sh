#!/bin/bash

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$BASE_DIR/ai-stack/core.sh"

ACTION=$1
SERVICE=$2

print_usage() {
  echo "Usage:"
  echo "./ai-stack.sh menu"
  echo "./ai-stack.sh start [service]"
  echo "./ai-stack.sh stop [service]"
  echo "./ai-stack.sh restart [service]"
  echo "./ai-stack.sh pause [service]"
  echo "./ai-stack.sh unpause [service]"
  echo "./ai-stack.sh status [service]"
  echo "./ai-stack.sh logs [service]"
  echo "./ai-stack.sh remove [service]"
  echo "./ai-stack.sh reset [service]"
  echo "./ai-stack.sh repair-network"
}

pause_prompt() {
  echo
  read -r -p "Press Enter to continue..."
}

confirm() {
  question=$1
  read -r -p "$question [y/N]: " answer
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

run_action() {
  action=$1
  service=$2

  if [ -z "$service" ]; then
    run_all "$action"
    return $?
  fi

  run_service "$service" "$action"
  if [ "$action" = "start" ] || [ "$action" = "restart" ]; then
    repair_network_links
  fi
}

run_status_summary() {
  echo
  echo "Service Status"
  echo "=============="
  for svc in $(get_services_in_start_order); do
    run_service "$svc" status
  done
}

choose_service_menu() {
  while true; do
    clear
    echo "Select Service"
    echo "=============="

    services=()
    while IFS= read -r svc; do
      services+=("$svc")
    done < <(get_services_in_start_order)
    services+=("back")

    select chosen in "${services[@]}"; do
      if [ -n "$chosen" ]; then
        if [ "$chosen" = "back" ]; then
          return 0
        fi
        service_action_menu "$chosen"
        break
      fi
      echo "Invalid selection."
    done
  done
}

service_action_menu() {
  selected_service=$1

  while true; do
    clear
    echo "Service: $selected_service"
    echo "=========================="
    echo "1) Start"
    echo "2) Stop"
    echo "3) Restart"
    echo "4) Pause"
    echo "5) Unpause"
    echo "6) Status"
    echo "7) Logs"
    echo "8) Remove"
    echo "9) Reset (remove container + data volume)"
    echo "0) Back"
    read -r -p "Choose action: " action_choice

    case "$action_choice" in
      1) run_action start "$selected_service"; pause_prompt ;;
      2) run_action stop "$selected_service"; pause_prompt ;;
      3) run_action restart "$selected_service"; pause_prompt ;;
      4) run_action pause "$selected_service"; pause_prompt ;;
      5) run_action unpause "$selected_service"; pause_prompt ;;
      6) run_action status "$selected_service"; pause_prompt ;;
      7) run_action logs "$selected_service" ;;
      8)
        if confirm "Remove '$selected_service' container?"; then
          run_action remove "$selected_service"
        fi
        pause_prompt
        ;;
      9)
        if confirm "Reset '$selected_service' (container + volume data)?"; then
          run_action reset "$selected_service"
        fi
        pause_prompt
        ;;
      0) return 0 ;;
      *) echo "Invalid selection."; pause_prompt ;;
    esac
  done
}

all_services_menu() {
  while true; do
    clear
    echo "All Services"
    echo "============"
    echo "1) Start all"
    echo "2) Stop all"
    echo "3) Restart all"
    echo "4) Pause all"
    echo "5) Unpause all"
    echo "6) Status all"
    echo "7) Remove all containers"
    echo "8) Reset all services (containers + service volumes)"
    echo "0) Back"
    read -r -p "Choose action: " action_choice

    case "$action_choice" in
      1) run_action start; pause_prompt ;;
      2) run_action stop; pause_prompt ;;
      3) run_action restart; pause_prompt ;;
      4) run_action pause; pause_prompt ;;
      5) run_action unpause; pause_prompt ;;
      6) run_status_summary; pause_prompt ;;
      7)
        if confirm "Remove ALL managed service containers?"; then
          run_action remove
        fi
        pause_prompt
        ;;
      8)
        if confirm "Reset ALL managed services and delete their volumes?"; then
          run_action reset
        fi
        pause_prompt
        ;;
      0) return 0 ;;
      *) echo "Invalid selection."; pause_prompt ;;
    esac
  done
}

interactive_menu() {
  while true; do
    clear
    echo "AI Stack Service Manager"
    echo "========================"
    echo "1) Manage one service"
    echo "2) Manage all services"
    echo "3) Repair shared network links"
    echo "4) Show service status summary"
    echo "0) Exit"
    read -r -p "Choose option: " menu_choice

    case "$menu_choice" in
      1) choose_service_menu ;;
      2) all_services_menu ;;
      3) repair_network_links; pause_prompt ;;
      4) run_status_summary; pause_prompt ;;
      0) exit 0 ;;
      *) echo "Invalid selection."; pause_prompt ;;
    esac
  done
}

case "$ACTION" in
  ""|menu)
    interactive_menu
    ;;
  start|stop|restart|pause|unpause|logs|remove|status)
    run_action "$ACTION" "$SERVICE"
    ;;
  repair-network)
    repair_network_links
    ;;
  reset)
    if [ -z "$SERVICE" ]; then
      if confirm "Reset ALL managed services (container + service volumes)?"; then
        run_action reset
      else
        echo "Reset cancelled."
      fi
    else
      if confirm "Reset '$SERVICE' (container + service volume)?"; then
        run_action reset "$SERVICE"
      else
        echo "Reset cancelled."
      fi
    fi
    ;;
  *)
    print_usage
    exit 1
    ;;
esac
