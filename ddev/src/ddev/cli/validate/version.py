import re
from functools import partial
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ddev.cli.application import Application

MISSING = object()


def _parse_changelog(path):
    raw = path.read_text()
    managed_by_towncrier = 'towncrier release notes start' in raw
    releases = re.findall(r'## (\d+\.\d+\.\d+)', raw)
    latest = releases[0] if releases else MISSING
    return managed_by_towncrier, latest


def _validate_python_package(
    track_err, repo_path, current_check, version_file, changelog, changelog_is_autogenerated, changelog_latest_v
):
    if not changelog_is_autogenerated:
        track_err(
            message=(
                f'This looks like a Python package, but {changelog.relative_to(repo_path)} is managed manually. '
                'Please add the towncrier header to the CHANGELOG.'
            )
        )
        return

    latest_pkg_v = re.findall(r"(\d+\.\d+\.\d+)", version_file.read_text())[0]

    # New integrations that are Python packages should have:
    # - autogenerated CHANGELOG that's empty
    # - version <1.0.0 in __about__.py
    if latest_pkg_v < '1.0.0':
        if changelog_latest_v is not MISSING:
            track_err(
                message=(
                    f'The version {latest_pkg_v} from {version_file.relative_to(repo_path)} means this integration '
                    f'has not been released yet. {changelog.relative_to(repo_path)} should not contain any '
                    'release sections.'
                )
            )
            return
        else:
            return

    # Python packages that have been released should have version >=1.0.0 and at least one CHANGELOG entry.
    # The latest CHANGELOG release should match the __about__.py version.
    if latest_pkg_v >= '1.0.0' and changelog_latest_v is MISSING:
        track_err(
            message=(
                f'Getting conflicting information. Version {latest_pkg_v} from {version_file.relative_to(repo_path)} '
                "indicates we have released the integration (it's >=1.0.0). "
                f'However {changelog.relative_to(repo_path)} contains no releases. '
                'Either we need to add a release to the CHANGELOG or roll back the version.'
            )
        )
        return
    if changelog_latest_v != latest_pkg_v:
        track_err(
            message=(
                f'Version {latest_pkg_v} from {version_file.relative_to(repo_path)} '
                f'does not match {changelog_latest_v} which is the latest version from '
                f'{changelog.relative_to(repo_path)}'
            )
        )
        return


def _validate_tile_only_int(
    track_err, repo_path, current_check, version_file, changelog, changelog_is_autogenerated, changelog_latest_v
):
    if changelog_is_autogenerated:
        track_err(
            message=(
                f'{changelog.relative_to(repo_path)} expects a Python package but the "{current_check.name}" '
                f'integration is missing {version_file.relative_to(repo_path)}.'
            )
        )
    else:
        if changelog_latest_v is MISSING:
            track_err(message=(f'Missing a version in {changelog.relative_to(repo_path)}, please add one.'))


@click.command()
@click.argument('integrations', nargs=-1)
@click.pass_context
def version(ctx: click.Context, integrations: tuple[str, ...]):
    """
    Check that the integration version is defined and makes sense.

    \b
    - It should exist.
    - In Python packages the CHANGELOG should be automatically generated and match __about__.py.
    - In new Python packages CHANGELOG should have no version and __about__.py should have 0.0.1 as the version.

    For now the validation is limited to integrations-core.
    INTEGRATIONS can be one or more integrations or the special value "all"
    """
    app: Application = ctx.obj
    if app.repo.name != 'core':
        app.display_info(f"Version validation is only available for repo `core`, skipping for repo `{app.repo.name}`")
        app.abort()

    tracker = app.create_validation_tracker('version')
    repo_path = app.repo.path

    if not integrations:
        integrations = ('all',)

    for project in app.repo.integrations.iter_all(selection=integrations):
        # ddev manages its version dynamically.
        if project.name == 'ddev':
            continue
        changelog = project.path / 'CHANGELOG.md'
        version_file = project.package_directory / ('_version.py' if project.name == 'ddev' else '__about__.py')
        is_python_pkg = version_file.exists()
        track_err = partial(tracker.error, (project.name,))

        if is_python_pkg and not changelog.exists():
            track_err(message=f'This looks like a Python package, but {changelog.relative_to(repo_path)} is missing.')
            continue

        changelog_is_autogenerated, changelog_latest_v = _parse_changelog(changelog)

        if is_python_pkg:
            validate = _validate_python_package
        elif project.is_tile:
            validate = _validate_tile_only_int
        else:
            continue
        validate(
            track_err,
            repo_path,
            project,
            version_file,
            changelog,
            changelog_is_autogenerated,
            changelog_latest_v,
        )

    tracker.display()
    if tracker.errors:
        app.abort()
    app.display_success('Version checks out.')
