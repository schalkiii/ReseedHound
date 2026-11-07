"""CLI 解析：公共开关在子命令前后都生效，子解析器不得覆盖父解析器已解析值。"""
import pytest

from run import CLI_DEFAULTS, COMMANDS, build_parser, parse_args


@pytest.mark.parametrize("argv,attr,expected", [
    (["--dry-run", "reseed"], "dry_run", True),
    (["--no-feishu", "reseed"], "no_feishu", True),
    (["--site", "A,B", "reseed"], "site", "A,B"),
    (["--mode", "jackett", "reseed"], "mode", "jackett"),
    (["--config", "c.yaml", "reseed"], "config", "c.yaml"),
    (["--sites-path", "s.yaml", "reseed"], "sites_path", "s.yaml"),
    (["reseed", "--dry-run"], "dry_run", True),
    (["schedule", "--mode", "both"], "mode", "both"),
    (["schedule", "--no-feishu"], "no_feishu", True),
    (["--dry-run", "schedule"], "dry_run", True),
])
def test_common_flags_take_effect(argv, attr, expected):
    assert getattr(parse_args(argv), attr) == expected


def test_all_common_flags_combined():
    args = parse_args([
        "--dry-run", "--no-feishu", "--site", "x", "--mode", "both",
        "--config", "c.yaml", "--sites-path", "s.yaml", "schedule",
    ])
    assert (args.dry_run, args.no_feishu, args.site, args.mode) == (
        True, True, "x", "both",
    )
    assert (args.config, args.sites_path, args.command) == (
        "c.yaml", "s.yaml", "schedule",
    )


def test_defaults_when_no_flags():
    args = parse_args(["reseed"])
    for key, value in CLI_DEFAULTS.items():
        assert getattr(args, key) == value


def test_no_subcommand_defaults():
    args = parse_args([])
    assert args.command is None
    assert args.dry_run is False
    assert args.config == "config.yaml"
    assert args.sites_path == "sites.yaml"


def test_all_subcommands_registered():
    parser = build_parser()
    assert parser.description == "SeedHound 辅种引擎"
    for command in COMMANDS:
        assert parse_args([command]).command == command


def test_sync_cookies_specific_flags():
    args = parse_args([
        "sync-cookies", "--cookiecloud-uuid", "u",
        "--cookiecloud-password", "p", "--cookiecloud-url", "http://cc",
    ])
    assert (args.cookiecloud_uuid, args.cookiecloud_password) == ("u", "p")
    assert args.cookiecloud_url == "http://cc"
    assert args.config == "config.yaml"


def test_invalid_mode_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--mode", "unknown", "reseed"])
