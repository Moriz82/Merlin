import os
from pathlib import Path
import shutil
import subprocess


def test_host_side_state_change_refuses_running_service(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    shutil.copy2(Path(__file__).parents[1] / 'manage.sh', project / 'manage.sh')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    docker = fake_bin / 'docker'
    docker.write_text(
        '#!/bin/sh\n'
        'case " $* " in\n'
        '  *" compose version "*) exit 0 ;;\n'
        '  *" ps --status running -q "*) echo synthetic-container-id; exit 0 ;;\n'
        'esac\n'
        'exit 99\n'
    )
    docker.chmod(0o700)
    result = subprocess.run(
        ['bash', str(project / 'manage.sh'), 'add-user', 'synthetic-user', 'scribe'],
        text=True,
        capture_output=True,
        env={**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH']},
    )
    assert result.returncode == 1
    assert result.stdout == ''
    assert 'Stop Merlin before a host-side state change.' in result.stderr
    assert not (project / 'state').exists()


def test_restore_container_uses_containerized_storage_marker_mode():
    script = (Path(__file__).parents[1] / 'manage.sh').read_text()
    restore = script.split('  restore)', 1)[1].split('  help|*)', 1)[0]
    assert '--network none' in restore
    assert '--env CONTAINERIZED=1' in restore


def test_password_commands_keep_an_interactive_terminal():
    script = (Path(__file__).parents[1] / 'manage.sh').read_text()
    assert 'run_cli_prompt() { ensure_team_network; compose run --rm --no-deps app ' in script
    assert 'run_cli_prompt add-user "$user" --role "$DEFAULT_ROLE"' in script
    assert 'run_cli_prompt add-user "$2" --role "$3"' in script
    assert 'run_cli_prompt ghostwriter --origin "$2"' in script


def test_workspace_directory_must_match_the_configured_runtime_identity():
    script = (Path(__file__).parents[1] / 'manage.sh').read_text()
    prepare = script.split('prepare_dirs() {', 1)[1].split('\n}', 1)[0]
    assert 'runtime_user=$(runtime_identity)' in prepare
    assert 'if [[ ! -e "$ROOT/state" ]]' in prepare
    assert 'The workspace directory owner does not match the configured application user.' in prepare
    assert 'The workspace directory permissions changed. Stop and inspect the workspace.' in prepare


def test_backup_uses_storage_identity_in_a_networkless_container():
    script = (Path(__file__).parents[1] / 'manage.sh').read_text()
    backup = script.split('  backup)', 1)[1].split('  restore)', 1)[0]
    assert 'docker run --rm --network none' in backup
    assert '--cap-drop ALL' in backup
    assert '--security-opt no-new-privileges:true' in backup
    assert '--env CONTAINERIZED=1' in backup
    assert 'state_uid=$(stat -c \'%u\' "$ROOT/state")' in backup
    assert 'state_gid=$(stat -c \'%g\' "$ROOT/state")' in backup
    assert 'runtime_user=$(runtime_identity)' in backup
    assert '"$runtime_user" == "$state_uid:$state_gid"' in backup
    assert '--user "$runtime_user"' in backup
    assert 'src=$ROOT/state,dst=/state,readonly' in backup
    assert 'mktemp -d "$parent/.merlin-backup.XXXXXXXX"' in backup
    assert 'chown -R --no-dereference "$(id -u):$(id -g)"' not in backup
    assert '"$destination" != "$ROOT/state"' in backup
    assert '"$destination" != "$ROOT/state/"*' in backup
    assert 'mv -T -n -- "$stage/$name" "$destination"' in backup
    assert 'preserve_stage_on_failure' in backup


def test_restore_uses_runtime_identity_and_atomic_staging():
    script = (Path(__file__).parents[1] / 'manage.sh').read_text()
    restore = script.split('  restore)', 1)[1].split('  help|*)', 1)[0]
    assert 'runtime_user=$(runtime_identity)' in restore
    assert '--user "$runtime_user"' in restore
    assert 'mktemp -d "$ROOT/.merlin-restore.XXXXXXXX"' in restore
    assert 'src=$stage,dst=/restore-root' in restore
    assert 'mv -T -n -- "$stage/state" "$ROOT/state"' in restore
    assert 'run_cli verify' not in restore
    assert restore.count('docker run --rm --network none') >= 2
    assert 'mark_encrypted_path "$stage"' in restore
    assert 'unlink -- "$stage/.storage-verified.json"' in restore
    assert '?mode=ro&immutable=1' in restore
