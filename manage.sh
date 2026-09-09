#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$ROOT"
APP=Merlin
DEFAULT_PORT=8711
DEFAULT_ROLE=lead_scribe

fail() { printf '%s\n' "$*" >&2; exit 1; }
need_docker() { docker compose version >/dev/null 2>&1 || fail 'Docker Compose is required.'; }
ensure_team_network() {
  if ! docker network inspect cptc-team-workspace >/dev/null 2>&1; then
    docker network create --driver bridge --label cptc.team.workspace=true cptc-team-workspace >/dev/null 2>&1 ||
      docker network inspect cptc-team-workspace >/dev/null 2>&1 || fail 'Cannot create the private team Docker network.'
  fi
}
prepare_env() {
  install -d -m 700 "$ROOT/tls"
  if [[ ! -f "$ROOT/.env" ]]; then
    {
      printf 'APP_UID=%s\n' "$(id -u)"
      printf 'APP_GID=%s\n' "$(id -g)"
      printf 'APP_VERSION=local\nAPP_PORT=%s\nHOST_BIND=127.0.0.1\nAPP_TLS_CERT=\nAPP_TLS_KEY=\n' "$DEFAULT_PORT"
    } > "$ROOT/.env"
    chmod 600 "$ROOT/.env"
  fi
}
prepare_dirs() {
  prepare_env
  local runtime_user runtime_uid runtime_gid owner permissions
  runtime_user=$(runtime_identity)
  runtime_uid=${runtime_user%%:*}
  runtime_gid=${runtime_user##*:}
  if [[ ! -e "$ROOT/state" ]]; then
    if [[ $(id -u) == 0 ]]; then
      install -d -o "$runtime_uid" -g "$runtime_gid" -m 700 "$ROOT/state"
    else
      [[ "$(id -u):$(id -g)" == "$runtime_user" ]] || fail 'Only root or the configured application user can create the workspace directory.'
      install -d -m 700 "$ROOT/state"
    fi
  fi
  [[ -d "$ROOT/state" && ! -L "$ROOT/state" ]] || fail 'The workspace directory is not a private directory.'
  owner=$(stat -c '%u:%g' "$ROOT/state")
  [[ "$owner" == "$runtime_user" ]] || fail 'The workspace directory owner does not match the configured application user.'
  permissions=$(stat -c '%a' "$ROOT/state")
  [[ "$permissions" == 700 ]] || fail 'The workspace directory permissions changed. Stop and inspect the workspace.'
}
compose() { docker compose --project-directory "$ROOT" "$@"; }
runtime_identity() {
  local value
  value=$(compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["app"].get("user", ""))') || fail 'Cannot identify the application storage user.'
  [[ "$value" =~ ^[0-9]+:[0-9]+$ ]] || fail 'The application storage user must be a numeric UID:GID.'
  printf '%s\n' "$value"
}
require_services_stopped() {
  local running
  running=$(compose ps --status running -q) || fail 'Cannot inspect the application services.'
  [[ -z "$running" ]] || fail 'Stop Merlin before a host-side state change. Run ./manage.sh stop, then retry.'
}
mark_encrypted_path() {
  local target=$1 source types stamp temp
  source=$(findmnt -n -o SOURCE -T "$target") || fail 'Cannot identify the storage filesystem.'
  types=$(lsblk -sn -o TYPE "${source%%\[*}" 2>/dev/null || true)
  grep -qx 'crypt' <<<"$types" || fail 'Client mode requires an approved encrypted block-device workspace.'
  stamp=$(date -u +'%Y-%m-%dT%H:%M:%S.%6NZ')
  temp="$target/.storage-verified.json.new"
  printf '{"encrypted":true,"schema_version":1,"verified_at":"%s"}\n' "$stamp" > "$temp"
  chmod 600 "$temp"
  mv -f -- "$temp" "$target/.storage-verified.json"
}
run_cli() { ensure_team_network; compose run --rm --no-deps -T app python -m workspace.cli --workspace /state "$@"; }
run_cli_prompt() { ensure_team_network; compose run --rm --no-deps app python -m workspace.cli --workspace /state "$@"; }

case "${1:-help}" in
  plan)
    need_docker
    printf '%s\n' "$APP uses Docker Compose, port ${DEFAULT_PORT}, and a private local state folder."
    compose config --quiet
    compose config --images
    ;;
  build)
    need_docker; prepare_dirs; compose build --pull
    ;;
  init)
    need_docker; require_services_stopped; prepare_dirs
    engagement=${2:-Synthetic practice}
    mode=${3:-synthetic}
    origin=${4:-http://127.0.0.1:${DEFAULT_PORT}}
    user=${5:-scribe-lead}
    engagement_id=${6:-}
    peer_origin=${7:-http://merlin:${DEFAULT_PORT}}
    [[ "$mode" == synthetic || "$mode" == client ]] || fail 'Mode must be synthetic or client.'
    [[ "$mode" == synthetic ]] || mark_encrypted_path "$ROOT/state"
    compose build
    init_args=(init --origin "$origin" --peer-origin "$peer_origin" --engagement "$engagement" --mode "$mode")
    [[ -z "$engagement_id" ]] || init_args+=(--engagement-id "$engagement_id")
    run_cli "${init_args[@]}"
    run_cli_prompt add-user "$user" --role "$DEFAULT_ROLE"
    run_cli keygen
    printf '%s\n' 'Workspace initialized. Run ./manage.sh up when you are ready.'
    ;;
  add-user)
    [[ $# -eq 3 ]] || fail 'Usage: ./manage.sh add-user NAME ROLE'
    need_docker; require_services_stopped; prepare_dirs; run_cli_prompt add-user "$2" --role "$3"
    ;;
  keygen)
    need_docker; require_services_stopped; prepare_dirs; run_cli keygen
    ;;
  info)
    need_docker; prepare_dirs; run_cli info
    ;;
  up)
    need_docker; prepare_dirs; ensure_team_network; compose up -d --wait
    ;;
  stop)
    need_docker; compose stop
    ;;
  down)
    need_docker; compose down
    ;;
  status)
    need_docker; compose ps
    ;;
  logs)
    need_docker; compose logs --tail 200 app
    ;;
  verify)
    need_docker; prepare_dirs; compose config --quiet; run_cli verify
    ;;
  peer-card)
    need_docker; prepare_dirs; run_cli peer-card
    ;;
  enroll)
    [[ $# -eq 3 ]] || fail 'Usage: ./manage.sh enroll CARD.json FINGERPRINT'
    need_docker; require_services_stopped; prepare_dirs
    [[ -f "$2" && ! -L "$2" ]] || fail 'Peer card must be a regular, non-symlink file.'
    card=$(realpath -- "$2")
    [[ $(stat -c '%u' "$card") == "$(id -u)" ]] || fail 'Peer card must be owned by the current user.'
    compose run --rm --no-deps -v "$card:/input/peer-card.json:ro" app python -m workspace.cli --workspace /state enroll /input/peer-card.json --fingerprint "$3"
    ;;
  ghostwriter)
    [[ $# -eq 5 || $# -eq 6 ]] || fail 'Usage: ./manage.sh ghostwriter ORIGIN REPORT-ID SEVERITY-ID FINDING-TYPE-ID [TOKEN-FILE]'
    need_docker; require_services_stopped; prepare_dirs
    if [[ $# -eq 6 ]]; then
      [[ -f "$6" && ! -L "$6" ]] || fail 'Token file must be a regular, non-symlink file.'
      token_file=$(realpath -- "$6")
      [[ $(stat -c '%u' "$token_file") == "$(id -u)" ]] || fail 'Token file must be owned by the current user.'
      [[ $(stat -c '%a' "$token_file") == 600 ]] || fail 'Token file permissions must be 0600.'
      compose run --rm --no-deps -T -v "$token_file:/input/ghostwriter.token:ro" app python -m workspace.cli --workspace /state ghostwriter --origin "$2" --report-id "$3" --severity-id "$4" --finding-type-id "$5" --token-file /input/ghostwriter.token
    else
      run_cli_prompt ghostwriter --origin "$2" --report-id "$3" --severity-id "$4" --finding-type-id "$5"
    fi
    ;;
  backup)
    [[ $# -eq 2 ]] || fail 'Usage: ./manage.sh backup NEW-DIRECTORY'
    need_docker; require_services_stopped; prepare_dirs
    destination=$(realpath -m -- "$2")
    [[ ! -e "$destination" ]] || fail 'The backup destination must be new.'
    [[ "$destination" != "$ROOT/state" && "$destination" != "$ROOT/state/"* ]] || fail 'The backup must be outside the live workspace.'
    parent=$(dirname -- "$destination")
    install -d -m 700 "$parent"
    client_backup=0
    if [[ -f "$ROOT/state/.storage-verified.json" ]]; then
      client_backup=1
    fi
    name=$(basename -- "$destination")
    state_uid=$(stat -c '%u' "$ROOT/state")
    state_gid=$(stat -c '%g' "$ROOT/state")
    [[ "$state_uid" =~ ^[0-9]+$ && "$state_gid" =~ ^[0-9]+$ ]] || fail 'Cannot identify the private storage owner.'
    runtime_user=$(runtime_identity)
    [[ "$runtime_user" == "$state_uid:$state_gid" ]] || fail 'The workspace owner does not match the configured application user.'
    stage=$(mktemp -d "$parent/.merlin-backup.XXXXXXXX")
    preserve_stage_on_failure() {
      local status=$? failed="$parent/merlin-backup-failed-$$"
      trap - EXIT
      if [[ $status -ne 0 && -d "${stage:-}" ]]; then
        mv -T -n -- "$stage" "$failed" || true
        [[ ! -d "$stage" ]] || failed=$stage
        printf 'Backup did not complete. Preserved private staging at %s\n' "$failed" >&2
      fi
      exit "$status"
    }
    trap preserve_stage_on_failure EXIT
    chown "$state_uid:$state_gid" "$stage"
    if [[ "$client_backup" == 1 ]]; then
      mark_encrypted_path "$stage"
      chown "$state_uid:$state_gid" "$stage/.storage-verified.json"
    fi
    image=$(compose config --images | sort -u)
    [[ -n "$image" && "$image" != *$'\n'* ]] || fail 'Cannot identify the application image.'
    docker image inspect "$image" >/dev/null 2>&1 || fail 'Build the application image before backup.'
    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --env CONTAINERIZED=1 \
      --user "$runtime_user" --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
      --mount "type=bind,src=$ROOT/state,dst=/state,readonly" \
      --mount "type=bind,src=$stage,dst=/backup" \
      "$image" python -m workspace.cli --workspace /state backup "/backup/$name"
    mv -T -n -- "$stage/$name" "$destination"
    [[ ! -e "$stage/$name" ]] || fail 'The backup destination appeared during publication. The private staging copy was preserved.'
    if [[ "$client_backup" == 1 ]]; then unlink -- "$stage/.storage-verified.json"; fi
    rmdir -- "$stage"
    trap - EXIT
    ;;
  restore)
    [[ $# -eq 2 ]] || fail 'Usage: ./manage.sh restore BACKUP-DIRECTORY'
    need_docker; require_services_stopped; prepare_env
    [[ ! -e "$ROOT/state" ]] || fail 'The state folder already exists. Stop the service and move that folder aside before restore.'
    [[ -d "$2" && ! -L "$2" ]] || fail 'The backup must be a regular directory.'
    backup_path=$(realpath -- "$2")
    runtime_user=$(runtime_identity)
    runtime_uid=${runtime_user%%:*}
    runtime_gid=${runtime_user##*:}
    [[ $(stat -c '%u' "$backup_path") == "$runtime_uid" ]] || fail 'The backup owner does not match the configured application user.'
    mode=$(python3 - "$backup_path/workspace.db" <<'PY'
import json, sqlite3, sys
connection = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro&immutable=1', uri=True)
row = connection.execute("SELECT value FROM settings WHERE key='config'").fetchone()
connection.close()
print(json.loads(row[0]).get('mode', '') if row else '')
PY
)
    [[ "$mode" == synthetic || "$mode" == client ]] || fail 'The backup does not contain a supported workspace.'
    image=$(compose config --images | sort -u)
    [[ -n "$image" && "$image" != *$'\n'* ]] || fail 'Cannot identify the application image.'
    docker image inspect "$image" >/dev/null 2>&1 || fail 'Build the application image before restore.'
    stage=$(mktemp -d "$ROOT/.merlin-restore.XXXXXXXX")
    preserve_stage_on_failure() {
      local status=$? failed="$ROOT/merlin-restore-failed-$$"
      trap - EXIT
      if [[ $status -ne 0 && -d "${stage:-}" ]]; then
        mv -T -n -- "$stage" "$failed" || true
        [[ ! -d "$stage" ]] || failed=$stage
        printf 'Restore did not complete. Preserved private staging at %s\n' "$failed" >&2
      fi
      exit "$status"
    }
    trap preserve_stage_on_failure EXIT
    chown "$runtime_uid:$runtime_gid" "$stage"
    if [[ "$mode" == client ]]; then
      mark_encrypted_path "$stage"
      chown "$runtime_uid:$runtime_gid" "$stage/.storage-verified.json"
    fi
    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --env CONTAINERIZED=1 \
      --user "$runtime_user" --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
      --mount "type=bind,src=$backup_path,dst=/backup,readonly" \
      --mount "type=bind,src=$stage,dst=/restore-root" \
      "$image" python -m workspace.cli --workspace /restore-root/state restore /backup
    mv -T -n -- "$stage/state" "$ROOT/state"
    [[ ! -e "$stage/state" ]] || fail 'The state destination appeared during publication. The private staging copy was preserved.'
    if [[ "$mode" == client ]]; then unlink -- "$stage/.storage-verified.json"; fi
    rmdir -- "$stage"
    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --env CONTAINERIZED=1 \
      --user "$runtime_user" --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
      --mount "type=bind,src=$ROOT/state,dst=/state" \
      "$image" python -m workspace.cli --workspace /state verify
    trap - EXIT
    printf '%s\n' 'Restore verified. Review the identity with ./manage.sh info before starting the service.'
    ;;
  help|*)
    cat <<'EOF'
Usage: ./manage.sh COMMAND

  plan                         Show the Docker plan.
  build                        Build the pinned image.
  init [ENGAGEMENT MODE ORIGIN USER ENGAGEMENT-ID PEER-ORIGIN]
                               Create a workspace and first account.
                               Use the same engagement ID on both hosts.
                               Synthetic Docker peer origin defaults to http://merlin:8711.
  add-user NAME ROLE           Add an individual account.
  keygen                       Create private transfer keys.
  up | stop | down | status    Control local containers.
  info | logs | verify         Inspect identity, service, and integrity state.
  peer-card                    Print the local pairing card.
  enroll CARD.json FINGERPRINT Enroll a verified peer.
  ghostwriter ORIGIN REPORT-ID SEVERITY-ID FINDING-TYPE-ID [TOKEN-FILE]
                               Verify one approved Ghostwriter report.
  backup NEW-DIRECTORY         Create a consistent local backup.
  restore BACKUP-DIRECTORY     Restore into a new state folder and verify it.
EOF
    ;;
esac
