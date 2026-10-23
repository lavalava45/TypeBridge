"""Named exclusion presets; exact executable basenames, never path wildcards."""

PASSWORD_PROCESSES = (
    '1password.exe', 'bitwarden.exe', 'credentialuibroker.exe', 'keepass.exe',
    'keepassxc.exe', 'logonui.exe', 'dashlane.exe', 'enpass.exe', 'nordpass.exe',
    'keeper.exe', 'roboform.exe',
)

CATEGORIES = {
    'development': ('IDE и разработка', (
        'code.exe', 'code - insiders.exe', 'vscodium.exe', 'cursor.exe', 'windsurf.exe',
        'devenv.exe', 'idea64.exe', 'idea.exe', 'pycharm64.exe', 'pycharm.exe',
        'webstorm64.exe', 'webstorm.exe', 'phpstorm64.exe', 'phpstorm.exe',
        'clion64.exe', 'clion.exe', 'rider64.exe', 'rider.exe', 'goland64.exe',
        'goland.exe', 'datagrip64.exe', 'datagrip.exe', 'rustrover64.exe',
        'rustrover.exe', 'studio64.exe', 'studio.exe', 'sublime_text.exe',
        'windowsterminal.exe', 'openconsole.exe', 'conhost.exe', 'cmd.exe',
        'powershell.exe', 'pwsh.exe',
    )),
    'creative': ('Графика, видео и звук', (
        'photoshop.exe', 'illustrator.exe', 'indesign.exe', 'afterfx.exe',
        'adobe premiere pro.exe', 'adobe audition.exe', 'adobe media encoder.exe',
        'lightroom.exe', 'lightroom classic.exe', 'resolve.exe', 'fusion.exe',
        'blender.exe', 'maya.exe', '3dsmax.exe', 'acad.exe', 'reaper.exe',
        'fl.exe', 'fl64.exe', 'studio one.exe', 'protools.exe',
        'cubase12.exe', 'cubase13.exe', 'cubase14.exe', 'cubase15.exe',
        'nuendo12.exe', 'nuendo13.exe', 'nuendo14.exe',
    )),
}
DEFAULT_CATEGORIES = tuple(CATEGORIES)


def effective_exclusions(settings):
    result = set(PASSWORD_PROCESSES)
    result.update(name.casefold() for name in settings.excluded_processes)
    for category in settings.excluded_categories:
        result.update(CATEGORIES[category][1])
    return frozenset(result)


def validate_categories(value):
    if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or item not in CATEGORIES for item in value):
        raise ValueError('Некорректная категория исключений')
    return tuple(dict.fromkeys(value))


def target_allowed(target, settings, process_name=None):
    """Fail open to the other app: uncertain targets are never intercepted."""
    if not target:
        return False
    if process_name is None:
        import psutil
        process_name = lambda pid: psutil.Process(pid).name()
    try:
        excluded = effective_exclusions(settings)
        return all(process_name(pid).casefold() not in excluded
                   for pid in {target.pid, target.host_pid or target.pid})
    except Exception:
        return False
