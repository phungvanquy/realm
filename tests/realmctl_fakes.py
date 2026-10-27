"""Small command doubles; native parsing and socket checks have separate tests."""

CHECKER = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert 'REALM_CONF' not in os.environ
if args == ['--version']:
    print('Realm 2.9.8 [test]')
elif args == ['check', '--version']:
    print('Realm-check 1')
elif args and args[0] == 'check':
    root = Path(os.environ['REALMCTL_TEST_ROOT'])
    with (root / 'checks').open('a') as log:
        log.write(json.dumps(args) + '\n')
    if '--service-only' in args:
        sys.exit(1 if (root / 'verify-fail').exists() else 0)
    config = Path(args[args.index('--config') + 1])
    files = [config] if config.is_file() else list(config.glob('*.toml'))
    for path in files:
        content = path.read_text()
        if not content.strip() or '# INVALID' in content:
            print('invalid configuration', file=sys.stderr)
            sys.exit(1)
        if '--pid' in args and '# NOT_READY' in content:
            print('expected listener is missing', file=sys.stderr)
            sys.exit(1)
    sys.exit(0)
else:
    sys.exit(2)
'''
