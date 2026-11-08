"""COM activation and warm IPC only; NEVER queries focused elements or text.

Run from the project root with:
  py -3.12 -B -m experiments.uia_native_startup_smoke_20261107
This is a source-only smoke, not a frozen-build verification.
"""

from time import perf_counter

from uia_native_worker import UiaService


def main():
    service = UiaService(timeout=3)
    try:
        started = perf_counter()
        first = service.request({'command': 'unknown-safety-smoke'})
        cold_ms = (perf_counter() - started) * 1000
        worker = service.process
        if first is not None or worker is None or not worker.is_alive():
            raise RuntimeError('Native COM worker did not start safely')
        pid = worker.pid
        started = perf_counter()
        second = service.request({'command': 'unknown-safety-smoke'})
        warm_ms = (perf_counter() - started) * 1000
        if second is not None or service.process.pid != pid:
            raise RuntimeError('Unexpected reply or helper restart')
        print(f'COM_INITIALIZED cold_ms={cold_ms:.1f} warm_ipc_ms={warm_ms:.1f} reused=True')
    finally:
        service.close()


if __name__ == '__main__':
    main()
