# verify — attempt 01

Command: `PYTHONPATH=src /Users/rem/harness-app/.venv/bin/python -m pytest -q`

Exit code: 1

```text
........................................................................ [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 20%]
............................................................F.F......... [ 26%]
....................FF.F...F........................FF.................. [ 31%]
........................................................................ [ 36%]
........................................................................ [ 41%]
........................................................................ [ 47%]
........................................................................ [ 52%]
........................................................................ [ 57%]
........................................................................ [ 62%]
........................................................................ [ 68%]
........................................................................ [ 73%]
........................................................................ [ 78%]
........................................................................ [ 83%]
........................................................................ [ 89%]
......................................................s................. [ 94%]
........................................................................ [ 99%]
.......                                                                  [100%]
=================================== FAILURES ===================================
__________ test_run_registers_label_issue_finisher_only_with_a_token ___________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x11019a5d0>
tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_registers_label_issue0')

    def test_run_registers_label_issue_finisher_only_with_a_token(monkeypatch, tmp_path):
        main(["init", "--root", str(tmp_path)])
        captured = {}
    
        def fake_build(*args, **kwargs):
            captured["finishers"] = kwargs.get("finishers")
            return object()
    
        async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0):
            pass
    
        monkeypatch.setattr("harness.cli.build", fake_build)
        monkeypatch.setattr("harness.cli.serve", fake_serve)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    
        assert main(["run", "--root", str(tmp_path)]) == 0
>       assert captured["finishers"] is None
E       AssertionError: assert {'open-issue': <function _run.<locals>.<lambda> at 0x10fe53ce0>} is None

tests/test_cli.py:329: AssertionError
----------------------------- Captured stdout call -----------------------------
harness ready at /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_registers_label_issue0
steps: plan, design, architecture, development, review, land
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_registers_label_issue0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
___________ test_run_without_heal_repo_wires_no_open_issue_finisher ____________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x1100eb710>
tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_without_heal_repo_wir0')

    def test_run_without_heal_repo_wires_no_open_issue_finisher(monkeypatch, tmp_path):
        main(["init", "--root", str(tmp_path)])
        captured = {}
    
        def fake_build(*args, **kwargs):
            captured["finishers"] = kwargs.get("finishers")
            captured["served_names"] = args[1]
            return object()
    
        async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0):
            pass
    
        monkeypatch.setattr("harness.cli.build", fake_build)
        monkeypatch.setattr("harness.cli.serve", fake_serve)
    
        assert main(["run", "--root", str(tmp_path)]) == 0
>       assert captured["finishers"] is None
E       AssertionError: assert {'open-issue': <function _run.<locals>.<lambda> at 0x11001b600>, 'label-issue': <function _run.<locals>.<lambda> at 0x110019120>} is None

tests/test_cli.py:385: AssertionError
----------------------------- Captured stdout call -----------------------------
harness ready at /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_without_heal_repo_wir0
steps: plan, design, architecture, development, review, land
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_without_heal_repo_wir0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
____________ test_run_serves_multiple_workflows_with_repeated_flag _____________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x1100e9250>
tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_serves_multiple_workf0')

    def test_run_serves_multiple_workflows_with_repeated_flag(monkeypatch, tmp_path):
        main(["init", "--root", str(tmp_path)])
        (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
        captured = {}
    
        async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0):
            captured["harness"] = harness
    
        monkeypatch.setattr("harness.cli.serve", fake_serve)
    
        assert main(
            [
                "run",
                "--root",
                str(tmp_path),
                "--workflow",
                "development",
                "--workflow",
                "hotfix",
            ]
        ) == 0
        # The scaffolded resolver workflow is served whenever its file exists.
>       assert set(captured["harness"].workflows) == {"development", "hotfix", "resolver"}
E       AssertionError: assert {'development...', 'resolver'} == {'development...', 'resolver'}
E         
E         Extra items in the left set:
E         'heal'
E         Use -v to get more diff

tests/test_cli.py:878: AssertionError
----------------------------- Captured stdout call -----------------------------
harness ready at /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_serves_multiple_workf0
steps: plan, design, architecture, development, review, land
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_serves_multiple_workf0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
__________ test_run_with_no_workflow_flag_serves_default_and_resolver __________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x10fde84d0>
tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_flag0')

    def test_run_with_no_workflow_flag_serves_default_and_resolver(monkeypatch, tmp_path):
        main(["init", "--root", str(tmp_path)])
        (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
        captured = {}
    
        async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0):
            captured["harness"] = harness
    
        monkeypatch.setattr("harness.cli.serve", fake_serve)
    
        assert main(["run", "--root", str(tmp_path)]) == 0
        # `hotfix` isn't served (not selected), but the scaffolded `resolver` is —
        # its definition exists, so it rides alongside the default (decoupled from
        # the mergeability watcher flag).
>       assert set(captured["harness"].workflows) == {"development", "resolver"}
E       AssertionError: assert {'development...', 'resolver'} == {'development', 'resolver'}
E         
E         Extra items in the left set:
E         'heal'
E         Use -v to get more diff

tests/test_cli.py:895: AssertionError
----------------------------- Captured stdout call -----------------------------
harness ready at /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_flag0
steps: plan, design, architecture, development, review, land
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_flag0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
___ test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow ___

self = <uvicorn.server.Server object at 0x1102f4890>, sockets = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await self.lifespan.startup()
        if self.lifespan.should_exit:
            sys.exit(STARTUP_FAILURE)
    
        config = self.config
    
        def create_protocol(
            _loop: asyncio.AbstractEventLoop | None = None,
        ) -> asyncio.Protocol:
            return config.http_protocol_class(  # type: ignore[call-arg]
                config=config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
                _loop=_loop,
            )
    
        loop = asyncio.get_running_loop()
    
        listeners: Sequence[socket.SocketType]
        if sockets is not None:  # pragma: full coverage
            # Explicitly passed a list of open sockets.
            # We use this when the server is run from a Gunicorn worker.
    
            def _share_socket(
                sock: socket.SocketType,
            ) -> socket.SocketType:  # pragma py-not-win32
                # Windows requires the socket be explicitly shared across
                # multiple workers (processes).
                from socket import fromshare  # type: ignore[attr-defined]
    
                sock_data = sock.share(os.getpid())  # type: ignore[attr-defined]
                return fromshare(sock_data)
    
            self.servers: list[asyncio.base_events.Server] = []
            for sock in sockets:
                is_windows = platform.system() == "Windows"
                if config.workers > 1 and is_windows:  # pragma: py-not-win32
                    sock = _share_socket(sock)  # type: ignore[assignment]
                server = await loop.create_server(create_protocol, sock=sock, ssl=config.ssl, backlog=config.backlog)
                self.servers.append(server)
            listeners = sockets
    
        elif config.fd is not None:  # pragma: py-win32
            # Use an existing socket, from a file descriptor.
            sock = socket.fromfd(config.fd, socket.AF_UNIX, socket.SOCK_STREAM)
            server = await loop.create_server(create_protocol, sock=sock, ssl=config.ssl, backlog=config.backlog)
            assert server.sockets is not None  # mypy
            listeners = server.sockets
            self.servers = [server]
    
        elif config.uds is not None:  # pragma: py-win32
            # Create a socket using UNIX domain socket.
            uds_perms = 0o666
            if os.path.exists(config.uds):
                uds_perms = os.stat(config.uds).st_mode  # pragma: full coverage
            server = await loop.create_unix_server(
                create_protocol, path=config.uds, ssl=config.ssl, backlog=config.backlog
            )
            os.chmod(config.uds, uds_perms)
            assert server.sockets is not None  # mypy
            listeners = server.sockets
            self.servers = [server]
    
        else:
            # Standard case. Create a socket from a host/port pair.
            try:
>               server = await loop.create_server(
                    create_protocol,
                    host=config.host,
                    port=config.port,
                    ssl=config.ssl,
                    backlog=config.backlog,
                )

../../../harness-app/.venv/lib/python3.11/site-packages/uvicorn/server.py:170: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <_UnixSelectorEventLoop running=False closed=True debug=False>
protocol_factory = <function Server.startup.<locals>.create_protocol at 0x10febef20>
host = '127.0.0.1', port = 8420, family = <AddressFamily.AF_UNSPEC: 0>
flags = <AddressInfo.AI_PASSIVE: 1>
sock = <socket.socket [closed] fd=-1, family=2, type=1, proto=6>, backlog = 2048
ssl = None, reuse_address = True, reuse_port = None
ssl_handshake_timeout = None, ssl_shutdown_timeout = None, start_serving = True

    async def create_server(
            self, protocol_factory, host=None, port=None,
            *,
            family=socket.AF_UNSPEC,
            flags=socket.AI_PASSIVE,
            sock=None,
            backlog=100,
            ssl=None,
            reuse_address=None,
            reuse_port=None,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None,
            start_serving=True):
        """Create a TCP server.
    
        The host parameter can be a string, in that case the TCP server is
        bound to host and port.
    
        The host parameter can also be a sequence of strings and in that case
        the TCP server is bound to all hosts of the sequence. If a host
        appears multiple times (possibly indirectly e.g. when hostnames
        resolve to the same IP address), the server is only bound once to that
        host.
    
        Return a Server object which can be used to stop the service.
    
        This method is a coroutine.
        """
        if isinstance(ssl, bool):
            raise TypeError('ssl argument must be an SSLContext or None')
    
        if ssl_handshake_timeout is not None and ssl is None:
            raise ValueError(
                'ssl_handshake_timeout is only meaningful with ssl')
    
        if ssl_shutdown_timeout is not None and ssl is None:
            raise ValueError(
                'ssl_shutdown_timeout is only meaningful with ssl')
    
        if sock is not None:
            _check_ssl_socket(sock)
    
        if host is not None or port is not None:
            if sock is not None:
                raise ValueError(
                    'host/port and sock can not be specified at the same time')
    
            if reuse_address is None:
                reuse_address = os.name == "posix" and sys.platform != "cygwin"
            sockets = []
            if host == '':
                hosts = [None]
            elif (isinstance(host, str) or
                  not isinstance(host, collections.abc.Iterable)):
                hosts = [host]
            else:
                hosts = host
    
            fs = [self._create_server_getaddrinfo(host, port, family=family,
                                                  flags=flags)
                  for host in hosts]
            infos = await tasks.gather(*fs)
            infos = set(itertools.chain.from_iterable(infos))
    
            completed = False
            try:
                for res in infos:
                    af, socktype, proto, canonname, sa = res
                    try:
                        sock = socket.socket(af, socktype, proto)
                    except socket.error:
                        # Assume it's a bad family/type/protocol combination.
                        if self._debug:
                            logger.warning('create_server() failed to create '
                                           'socket.socket(%r, %r, %r)',
                                           af, socktype, proto, exc_info=True)
                        continue
                    sockets.append(sock)
                    if reuse_address:
                        sock.setsockopt(
                            socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
                    if reuse_port:
                        _set_reuseport(sock)
                    # Disable IPv4/IPv6 dual stack support (enabled by
                    # default on Linux) which makes a single socket
                    # listen on both address families.
                    if (_HAS_IPv6 and
                            af == socket.AF_INET6 and
                            hasattr(socket, 'IPPROTO_IPV6')):
                        sock.setsockopt(socket.IPPROTO_IPV6,
                                        socket.IPV6_V6ONLY,
                                        True)
                    try:
                        sock.bind(sa)
                    except OSError as err:
                        msg = ('error while attempting '
                               'to bind on address %r: %s'
                               % (sa, err.strerror.lower()))
                        if err.errno == errno.EADDRNOTAVAIL:
                            # Assume the family is not enabled (bpo-30945)
                            sockets.pop()
                            sock.close()
                            if self._debug:
                                logger.warning(msg)
                            continue
>                       raise OSError(err.errno, msg) from None
E                       OSError: [Errno 48] error while attempting to bind on address ('127.0.0.1', 8420): address already in use

../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/base_events.py:1536: OSError

During handling of the above exception, another exception occurred:

tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_all_workflows_without0')
capsys = <_pytest.capture.CaptureFixture object at 0x10fee8890>

    def test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow(tmp_path, capsys):
        """Serving the dormant `heal` workflow without `--heal-repo` means nothing
        registers its `file-issue` step's "open-issue" finisher kind — `build()`
        refuses at startup (fail-fast configuration), not mid-run."""
        main(["init", "--root", str(tmp_path)])
        capsys.readouterr()
    
>       assert main(["run", "--root", str(tmp_path), "--all-workflows"]) == 2
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_cli.py:924: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/harness/cli.py:2101: in main
    return args.handler(args)
           ^^^^^^^^^^^^^^^^^^
src/harness/cli.py:1754: in _run
    asyncio.run(
../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/runners.py:190: in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/runners.py:118: in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/base_events.py:641: in run_until_complete
    self.run_forever()
../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/base_events.py:608: in run_forever
    self._run_once()
../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/base_events.py:1936: in _run_once
    handle._run()
../../../.local/share/uv/python/cpython-3.11.15-macos-x86_64-none/lib/python3.11/asyncio/events.py:84: in _run
    self._context.run(self._callback, *self._args)
../../../harness-app/.venv/lib/python3.11/site-packages/uvicorn/server.py:78: in serve
    await self._serve(sockets)
../../../harness-app/.venv/lib/python3.11/site-packages/uvicorn/server.py:93: in _serve
    await self.startup(sockets=sockets)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <uvicorn.server.Server object at 0x1102f4890>, sockets = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await self.lifespan.startup()
        if self.lifespan.should_exit:
            sys.exit(STARTUP_FAILURE)
    
        config = self.config
    
        def create_protocol(
            _loop: asyncio.AbstractEventLoop | None = None,
        ) -> asyncio.Protocol:
            return config.http_protocol_class(  # type: ignore[call-arg]
                config=config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
                _loop=_loop,
            )
    
        loop = asyncio.get_running_loop()
    
        listeners: Sequence[socket.SocketType]
        if sockets is not None:  # pragma: full coverage
            # Explicitly passed a list of open sockets.
            # We use this when the server is run from a Gunicorn worker.
    
            def _share_socket(
                sock: socket.SocketType,
            ) -> socket.SocketType:  # pragma py-not-win32
                # Windows requires the socket be explicitly shared across
                # multiple workers (processes).
                from socket import fromshare  # type: ignore[attr-defined]
    
                sock_data = sock.share(os.getpid())  # type: ignore[attr-defined]
                return fromshare(sock_data)
    
            self.servers: list[asyncio.base_events.Server] = []
            for sock in sockets:
                is_windows = platform.system() == "Windows"
                if config.workers > 1 and is_windows:  # pragma: py-not-win32
                    sock = _share_socket(sock)  # type: ignore[assignment]
                server = await loop.create_server(create_protocol, sock=sock, ssl=config.ssl, backlog=config.backlog)
                self.servers.append(server)
            listeners = sockets
    
        elif config.fd is not None:  # pragma: py-win32
            # Use an existing socket, from a file descriptor.
            sock = socket.fromfd(config.fd, socket.AF_UNIX, socket.SOCK_STREAM)
            server = await loop.create_server(create_protocol, sock=sock, ssl=config.ssl, backlog=config.backlog)
            assert server.sockets is not None  # mypy
            listeners = server.sockets
            self.servers = [server]
    
        elif config.uds is not None:  # pragma: py-win32
            # Create a socket using UNIX domain socket.
            uds_perms = 0o666
            if os.path.exists(config.uds):
                uds_perms = os.stat(config.uds).st_mode  # pragma: full coverage
            server = await loop.create_unix_server(
                create_protocol, path=config.uds, ssl=config.ssl, backlog=config.backlog
            )
            os.chmod(config.uds, uds_perms)
            assert server.sockets is not None  # mypy
            listeners = server.sockets
            self.servers = [server]
    
        else:
            # Standard case. Create a socket from a host/port pair.
            try:
                server = await loop.create_server(
                    create_protocol,
                    host=config.host,
                    port=config.port,
                    ssl=config.ssl,
                    backlog=config.backlog,
                )
            except OSError as exc:
                logger.error(exc)
                await self.lifespan.shutdown()
>               sys.exit(STARTUP_FAILURE)
E               SystemExit: 3

../../../harness-app/.venv/lib/python3.11/site-packages/uvicorn/server.py:180: SystemExit
----------------------------- Captured stdout call -----------------------------
started workflows=['development', 'heal', 'resolver']
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_all_workflows_without0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8420): address already in use
_______ test_run_single_custom_workflow_ignores_github_workflow_default ________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x10fb67210>
tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_single_custom_workflo0')
capsys = <_pytest.capture.CaptureFixture object at 0x10fb64510>

    def test_run_single_custom_workflow_ignores_github_workflow_default(
        monkeypatch, tmp_path, capsys
    ):
        """Regression: `--github-workflow` used to default to `DEFAULT_WORKFLOW`
        ("default") and get checked against the served set unconditionally, so
        `run --workflow hotfix` with no GitHub flags at all (and no GITHUB_TOKEN)
        used to fail startup even though no GithubTaskSource is ever built in that
        case. FR-6 requires single-workflow runs to behave exactly as before."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        main(["init", "--root", str(tmp_path)])
        (tmp_path / "workflows" / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
        captured = {}
    
        async def fake_serve(harness, port, poll_interval, source_interval=30.0, pr_poll_interval=0.0, reconcile_interval=300.0):
            captured["harness"] = harness
    
        monkeypatch.setattr("harness.cli.serve", fake_serve)
        capsys.readouterr()
    
        assert main(["run", "--root", str(tmp_path), "--workflow", "hotfix"]) == 0
    
        out, err = capsys.readouterr()
>       assert err == ""
E       AssertionError: assert 'warning: hea...le nothing.\n' == ''
E         
E         + warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_single_custom_workflo0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.

tests/test_cli.py:997: AssertionError
_______________ test_run_resolves_default_workflow_when_omitted ________________

tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_resolves_default_work0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x1100ffa50>

    def test_run_resolves_default_workflow_when_omitted(tmp_path, monkeypatch):
        """Plain `harness run` (no --workflow) against an ordinarily-initialized
        harness serves `development` — the same effective default as before
        --workflow's argparse default became None to support --no-workflow
        harnesses. The scaffolded `resolver` workflow is appended because its
        definition exists."""
        main(["init", "--root", str(tmp_path)])
        seen = {}
    
        def fake_build(root, served, **kwargs):
            seen["served"] = served
            raise SystemExit(0)
    
        monkeypatch.setattr("harness.cli.build", fake_build)
    
        with pytest.raises(SystemExit):
            main(["run", "--root", str(tmp_path), "--api-port", "0"])
    
>       assert list(seen["served"]) == ["development", "resolver"]
E       AssertionError: assert ['development...lver', 'heal'] == ['development', 'resolver']
E         
E         Left contains one more item: 'heal'
E         Use -v to get more diff

tests/test_cli.py:1468: AssertionError
----------------------------- Captured stdout call -----------------------------
harness ready at /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_resolves_default_work0
steps: plan, design, architecture, development, review, land
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_resolves_default_work0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
______________ test_run_with_no_workflow_harness_defaults_to_none ______________

tmp_path = PosixPath('/private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_harn0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x10f51b750>

    def test_run_with_no_workflow_harness_defaults_to_none(tmp_path, monkeypatch):
        """A --no-workflow harness has no workflows/default.json, so an omitted
        --workflow flag must resolve to an empty served set (workflow-less), not
        raise WorkflowNotFound."""
        main(["init", "--root", str(tmp_path), "--no-workflow"])
        seen = {}
    
        def fake_build(root, served, **kwargs):
            seen["served"] = served
            raise SystemExit(0)
    
        monkeypatch.setattr("harness.cli.build", fake_build)
    
        with pytest.raises(SystemExit):
            main(["run", "--root", str(tmp_path), "--api-port", "0"])
    
>       assert seen["served"] == ()
E       AssertionError: assert ['heal'] == ()
E         
E         Left contains one more item: 'heal'
E         Use -v to get more diff

tests/test_cli.py:1487: AssertionError
----------------------------- Captured stdout call -----------------------------
harness ready at /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_harn0 (no workflow — add steps under /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_harn0/agents)
----------------------------- Captured stderr call -----------------------------
warning: heal repo 'onpaj/harness_v2' is not registered in /private/var/folders/tf/q_3r9q192_ggzl3802wmxbch0000gn/T/pytest-of-rem/pytest-867/test_run_with_no_workflow_harn0/repos.json — heal tasks will fail to attach a worktree until it is added there, so self-healing will file nothing.
=============================== warnings summary ===============================
../../../harness-app/.venv/lib/python3.11/site-packages/fastapi/testclient.py:1
  /Users/rem/harness-app/.venv/lib/python3.11/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/test_cli.py::test_run_registers_label_issue_finisher_only_with_a_token
FAILED tests/test_cli.py::test_run_without_heal_repo_wires_no_open_issue_finisher
FAILED tests/test_cli.py::test_run_serves_multiple_workflows_with_repeated_flag
FAILED tests/test_cli.py::test_run_with_no_workflow_flag_serves_default_and_resolver
FAILED tests/test_cli.py::test_run_all_workflows_without_heal_repo_fails_fast_on_the_heal_workflow
FAILED tests/test_cli.py::test_run_single_custom_workflow_ignores_github_workflow_default
FAILED tests/test_cli.py::test_run_resolves_default_workflow_when_omitted - A...
FAILED tests/test_cli.py::test_run_with_no_workflow_harness_defaults_to_none
8 failed, 1366 passed, 1 skipped, 1 warning in 47.44s

```
