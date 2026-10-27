"""Bounded callback diagnostics without exception messages, locals or user text."""
from datetime import datetime, timezone
from pathlib import Path
import traceback
from diagnostic_identity import computer_id


def record_runtime_event(folder, code):
    # Only internal outcome codes, never exception arguments, keys or word text.
    path = Path(folder) / 'replacement-diagnostics.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 65536:
        path.write_text('', encoding='utf-8')
    with path.open('a', encoding='utf-8') as stream:
        stream.write(f'{datetime.now(timezone.utc).isoformat()} [pc={computer_id()}] {code}\n')
    return path


def record_callback_error(folder, exception_type, tb):
    path = Path(folder) / 'ui-errors.log'
    frames = traceback.extract_tb(tb)
    trace = '\n'.join(f'{Path(frame.filename).name}:{frame.lineno} {frame.name}' for frame in frames)
    record = f'{datetime.now(timezone.utc).isoformat()} [pc={computer_id()}] {exception_type.__name__}\n{trace}\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding='utf-8') if path.exists() and path.stat().st_size < 65536 else ''
    path.write_text(previous + record, encoding='utf-8')
    return path


def install_callback_reporting(root, folder):
    def report(exception_type, value, tb):
        from ui_i18n import messagebox
        try:
            record_callback_error(folder, exception_type, tb)
        except Exception:
            pass
        # Do not display exception arguments: they can contain clipboard or document text.
        messagebox.showerror('TypeBridge',
                             f'Ошибка интерфейса: {exception_type.__name__}. Диагностика: TypeBridge-data\\ui-errors.log',
                             parent=root)
    root.report_callback_exception = report
