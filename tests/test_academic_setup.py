"""Comprobación del equipo para Capture (`python -m academic_setup check`)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

from academic_setup.__main__ import main

GB = 1024 ** 3
Usage = namedtuple("Usage", "total used free")


def nvidia(*lines, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout="\n".join(lines) + "\n", stderr="")


@contextlib.contextmanager
def machine(gpu=FileNotFoundError(), ram=16, cores=8, disk=100, modules=(), ffmpeg=True,
            env_runtime=None):
    """Equipo simulado: `gpu` es la respuesta o excepción de nvidia-smi; ram/disco en GB."""
    def run(cmd, **kwargs):
        assert cmd[0] == "nvidia-smi" and kwargs.get("timeout"), (cmd, kwargs)
        if isinstance(gpu, BaseException):
            raise gpu
        return gpu

    def ram_bytes():
        if isinstance(ram, BaseException):
            raise ram
        return int(ram * GB)

    def usage(path):
        if isinstance(disk, BaseException):
            raise disk
        return Usage(500 * GB, 0, int(disk * GB))

    env = {"ACADEMIC_MANAGER_DATA": env_runtime} if env_runtime else {}
    with patch("academic_setup.hardware.subprocess.run", side_effect=run) as smi, \
            patch("academic_setup.hardware._ram_bytes", side_effect=ram_bytes), \
            patch("academic_setup.hardware.os.cpu_count", return_value=cores), \
            patch("academic_setup.hardware.shutil.disk_usage", side_effect=usage) as du, \
            patch("academic_setup.hardware._module_present", side_effect=lambda name: name in modules), \
            patch("academic_setup.hardware.shutil.which",
                  side_effect=lambda name: "/bin/ffmpeg" if ffmpeg and name == "ffmpeg" else None), \
            patch.dict(os.environ, env):
        if not env_runtime:
            os.environ.pop("ACADEMIC_MANAGER_DATA", None)
        yield {"nvidia-smi": smi, "disk_usage": du}


def check(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["check", *argv])
    return code, out.getvalue()


def check_json(*argv):
    code, text = check(*argv)
    return code, json.loads(text)


class VerdictTests(unittest.TestCase):
    def test_gpu_with_4gb_vram_is_apto_gpu(self):
        with machine(gpu=nvidia("NVIDIA GeForce GTX 1650, 4096"), ram=8, cores=4, disk=20) as m:
            code, out = check_json()
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        data = out["data"]
        self.assertEqual(data["veredicto"], "apto_gpu")
        self.assertEqual(data["equipo"]["gpu_nvidia"]["valor"],
                         [{"nombre": "NVIDIA GeForce GTX 1650", "vram_gb": 4.0}])
        self.assertEqual(data["recomendacion"], {"modelo": "large-v3", "dispositivo": "auto"})
        self.assertTrue(m["nvidia-smi"].call_args.kwargs["timeout"])

    def test_gpu_below_4gb_falls_back_to_cpu_with_reasons(self):
        with machine(gpu=nvidia("NVIDIA GeForce MX150, 2048"), ram=16, cores=8, disk=20):
            _, out = check_json()
        data = out["data"]
        self.assertEqual(data["veredicto"], "solo_cpu")
        self.assertTrue(any("2.0 GB" in m and "4 GB" in m for m in data["motivos"]), data["motivos"])
        self.assertEqual(data["recomendacion"], {"modelo": "medium", "dispositivo": "cpu"})

    def test_no_gpu_with_enough_ram_and_cores_recommends_small(self):
        with machine(ram=8, cores=4, disk=5):
            _, out = check_json()
        data = out["data"]
        self.assertEqual(data["veredicto"], "solo_cpu")
        self.assertEqual(data["equipo"]["gpu_nvidia"]["valor"], [])
        self.assertEqual(data["recomendacion"], {"modelo": "small", "dispositivo": "cpu"})
        self.assertTrue(any("NVIDIA" in m for m in data["motivos"]))

    def test_16gb_ram_without_gpu_recommends_medium(self):
        with machine(ram=16, cores=4):
            _, out = check_json()
        self.assertEqual(out["data"]["recomendacion"]["modelo"], "medium")

    def test_ram_reported_below_nominal_counts_as_the_nominal_size(self):
        # Windows informa ~7.4–7.9 GB en un equipo vendido con 8 GB (y ~15.4–15.9 con 16).
        with machine(ram=7.4, cores=4, disk=40):
            _, out = check_json()
        self.assertEqual(out["data"]["veredicto"], "solo_cpu")
        self.assertEqual(out["data"]["recomendacion"]["modelo"], "small")
        with machine(ram=15.4, cores=4):
            _, out = check_json()
        self.assertEqual(out["data"]["recomendacion"]["modelo"], "medium")
        with machine(ram=7.2, cores=4, disk=40):
            _, out = check_json()
        self.assertEqual(out["data"]["veredicto"], "no_apto")

    def test_low_ram_is_no_apto(self):
        with machine(ram=4, cores=8):
            _, out = check_json()
        data = out["data"]
        self.assertEqual(data["veredicto"], "no_apto")
        self.assertIsNone(data["recomendacion"])
        self.assertTrue(any("RAM" in m and "8 GB" in m for m in data["motivos"]), data["motivos"])

    def test_few_cores_is_no_apto(self):
        with machine(cores=2):
            _, out = check_json()
        self.assertEqual(out["data"]["veredicto"], "no_apto")
        self.assertTrue(any("núcleos" in m for m in out["data"]["motivos"]))

    def test_low_disk_is_no_apto(self):
        with machine(disk=3):
            _, out = check_json()
        data = out["data"]
        self.assertEqual(data["veredicto"], "no_apto")
        self.assertTrue(any("disco" in m.lower() and "5 GB" in m for m in data["motivos"]), data["motivos"])

    def test_gpu_with_too_little_disk_for_gpu_install_falls_back_to_cpu(self):
        with machine(gpu=nvidia("NVIDIA GeForce RTX 3060, 12288"), disk=8):
            _, out = check_json()
        data = out["data"]
        self.assertEqual(data["veredicto"], "solo_cpu")
        self.assertTrue(any("12 GB" in m for m in data["motivos"]), data["motivos"])

    def test_always_works_is_reported_for_every_verdict(self):
        for kwargs in ({"gpu": nvidia("RTX, 8192")}, {}, {"ram": 2}):
            with machine(**kwargs):
                _, out = check_json()
            text = " ".join(out["data"]["siempre_funciona"])
            self.assertIn("YouTube", text)
            self.assertIn("transcripciones", text)


class ProbeTests(unittest.TestCase):
    def test_probe_failures_become_unknown_without_crash(self):
        with machine(gpu=subprocess.TimeoutExpired("nvidia-smi", 15), ram=OSError("x"), cores=None,
                     disk=PermissionError("x")):
            code, out = check_json()
        self.assertEqual(code, 0)
        equipo = out["data"]["equipo"]
        for key in ("gpu_nvidia", "ram_total_gb", "nucleos_cpu", "disco_libre_gb"):
            self.assertEqual(equipo[key]["valor"], "desconocido", key)
            self.assertTrue(equipo[key]["motivo"], key)
        self.assertEqual(out["data"]["veredicto"], "no_apto")
        self.assertTrue(any("No se pudo comprobar" in m for m in out["data"]["motivos"]))

    def test_nvidia_smi_failure_code_is_unknown(self):
        with machine(gpu=nvidia("", returncode=9)):
            _, out = check_json()
        gpu = out["data"]["equipo"]["gpu_nvidia"]
        self.assertEqual(gpu["valor"], "desconocido")
        self.assertIn("9", gpu["motivo"])
        self.assertEqual(out["data"]["veredicto"], "solo_cpu")

    def test_torch_absent_present_and_broken(self):
        with machine():
            _, out = check_json()
        self.assertEqual(out["data"]["equipo"]["torch_cuda"]["valor"], "no_instalado")
        torch = MagicMock()
        torch.cuda.is_available.return_value = True
        torch.cuda.device_count.return_value = 1
        torch.cuda.get_device_properties.return_value = MagicMock(total_memory=6 * GB)
        torch.cuda.get_device_properties.return_value.name = "RTX 2060"
        with machine(modules=("torch",)), patch.dict(sys.modules, {"torch": torch}):
            _, out = check_json()
        self.assertEqual(out["data"]["equipo"]["torch_cuda"],
                         {"valor": True, "gpus": [{"nombre": "RTX 2060", "vram_gb": 6.0}]})
        # Sin nvidia-smi pero con CUDA en PyTorch: la VRAM de PyTorch decide.
        self.assertEqual(out["data"]["veredicto"], "apto_gpu")
        torch.cuda.is_available.side_effect = RuntimeError("driver")
        with machine(modules=("torch",)), patch.dict(sys.modules, {"torch": torch}):
            _, out = check_json()
        self.assertEqual(out["data"]["equipo"]["torch_cuda"]["valor"], "desconocido")

    def test_programs_installed_flags(self):
        with machine(modules=("whisperx", "yt_dlp"), ffmpeg=False):
            _, out = check_json()
        self.assertEqual({k: v["valor"] for k, v in out["data"]["programas"].items()},
                         {"whisperx": True, "ffmpeg": False, "yt_dlp": True})

    def test_disk_measured_on_runtime_or_existing_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no" / "existe"
            with machine(env_runtime=tmp) as m:
                check_json()
            self.assertEqual(Path(m["disk_usage"].call_args.args[0]), Path(tmp).absolute())
            with machine(env_runtime="/ignored") as m:
                check_json("--runtime", str(missing))
            self.assertEqual(Path(m["disk_usage"].call_args.args[0]), Path(tmp).absolute())

    def test_check_never_writes_to_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with machine():
                check_json("--runtime", tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class OutputTests(unittest.TestCase):
    def test_texto_is_spanish_summary_not_json(self):
        with machine(ram=8, cores=4, disk=40):
            code, text = check("--texto")
        self.assertEqual(code, 0)
        self.assertNotIn('"ok"', text)
        for fragment in ("Resultado: SOLO CPU", "Memoria RAM: 8.0 GB", "small", "YouTube",
                         "Tarjeta gráfica compatible (NVIDIA): no tiene",
                         "Falta instalar WhisperX"):
            self.assertIn(fragment, text)
        # Sin jerga para una persona no técnica.
        for jargon in ("VRAM", "PyTorch", "CUDA", "--transcript", "JSON", "factor de tiempo real",
                       "--device", "núcleos"):
            self.assertNotIn(jargon, text)

    def test_texto_with_gpu_speaks_of_the_card_memory(self):
        gpu = subprocess.CompletedProcess([], 0, "NVIDIA GeForce RTX 3060, 12288\n", "")
        with machine(gpu=gpu, modules=("whisperx",)):
            _, text = check("--texto")
        self.assertIn("NVIDIA GeForce RTX 3060 con 12.0 GB de memoria propia", text)
        self.assertNotIn("VRAM", text)
        self.assertNotIn("Falta instalar WhisperX", text)

    def test_runtime_comes_from_the_user_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "datos"
            data.mkdir()
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"runtime": str(data)}), encoding="utf-8")
            with machine() as probes, patch.dict(os.environ, {"ACADEMIC_MANAGER_CONFIG": str(config)}):
                code, _ = check_json()
            self.assertEqual(code, 0)
            self.assertEqual(Path(probes["disk_usage"].call_args[0][0]), data)

    def test_invalid_config_file_is_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text("[]", encoding="utf-8")
            with machine(), patch.dict(os.environ, {"ACADEMIC_MANAGER_CONFIG": str(config)}):
                code, out = check_json()
        self.assertEqual((code, out["ok"], out["error"]["code"]), (2, False, "INVALID_CONFIG"))
        self.assertIn("config.json", out["error"]["message"])

    def test_invalid_argument_is_structured_error(self):
        code, out = check_json("--desconocida")
        self.assertEqual(code, 2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "INVALID_ARGUMENT")


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_unavailable_without_whisperx(self):
        with machine():
            _, out = check_json("--benchmark")
        bench = out["data"]["benchmark"]
        self.assertFalse(bench["disponible"])
        self.assertIn("WhisperX", bench["motivo"])

    def test_no_benchmark_key_without_flag(self):
        with machine():
            _, out = check_json()
        self.assertNotIn("benchmark", out["data"])

    def fake_whisperx(self, segments):
        torch, whisperx = MagicMock(), MagicMock()
        torch.cuda.is_available.return_value = False
        model = MagicMock()
        model.transcribe.return_value = {"segments": segments}
        whisperx.load_model.return_value = model
        seen = []
        whisperx.load_audio.side_effect = lambda path: seen.append(Path(path)) or b"audio"
        return torch, whisperx, seen

    def test_benchmark_measures_real_time_factor_and_cleans_up(self):
        torch, whisperx, seen = self.fake_whisperx([{"start": 0, "end": 1, "text": "a"}])
        with tempfile.TemporaryDirectory() as runtime, machine(modules=("whisperx",), ram=8, cores=4), \
                patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}), \
                patch("academic_setup.benchmark.clock", side_effect=[100.0, 130.0]):
            code, text = check("--benchmark", "--runtime", runtime)
            self.assertTrue((Path(runtime) / "procesamiento" / "model-cache").is_dir())
        out = json.loads(text)  # lo que imprime WhisperX no contamina el JSON
        bench = out["data"]["benchmark"]
        self.assertEqual(code, 0)
        self.assertTrue(bench["disponible"])
        self.assertEqual(bench["modelo"], "small")
        self.assertEqual(bench["dispositivo"], "cpu")
        self.assertEqual(bench["duracion_muestra_s"], 60)
        self.assertEqual(bench["factor_tiempo_real"], 0.5)
        self.assertEqual(bench["estimacion_clase_90_min_min"], 45)
        self.assertEqual(whisperx.load_model.call_args.args[:2], ("small", "cpu"))
        self.assertEqual(len(seen), 2)  # calentamiento + medición
        self.assertTrue(all(not p.exists() and not p.parent.exists() for p in seen))

    def test_benchmark_without_detected_speech_is_not_guessed(self):
        torch, whisperx, _ = self.fake_whisperx([])
        with tempfile.TemporaryDirectory() as runtime, machine(modules=("whisperx",)), \
                patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}):
            _, out = check_json("--benchmark", "--runtime", runtime)
        bench = out["data"]["benchmark"]
        self.assertFalse(bench["disponible"])
        self.assertNotIn("factor_tiempo_real", bench)

    def test_benchmark_failure_is_reported_not_raised(self):
        torch, whisperx, _ = self.fake_whisperx([])
        whisperx.load_model.side_effect = ValueError("broken")
        with tempfile.TemporaryDirectory() as runtime, machine(modules=("whisperx",)), \
                patch.dict(sys.modules, {"torch": torch, "whisperx": whisperx}):
            code, out = check_json("--benchmark", "--runtime", runtime)
        self.assertEqual(code, 0)
        self.assertFalse(out["data"]["benchmark"]["disponible"])

    def test_texto_reports_benchmark(self):
        with machine():
            _, text = check("--texto", "--benchmark")
        self.assertIn("Medición de velocidad: no disponible", text)


class WhisperXInstallTests(unittest.TestCase):
    """academic_setup.whisperx: instala solo si el veredicto lo permite, en segundo plano y reanudable."""

    def setUp(self):
        from academic_setup import whisperx
        self.wx = whisperx
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "datos"
        self.runtime.mkdir()
        self.python = Path(self.tmp.name) / "python" / "python.exe"

    def test_verdict_decides_the_torch_build(self):
        gpu, cpu = self.wx.plan("apto_gpu"), self.wx.plan("solo_cpu")
        self.assertEqual(gpu["variante"], "cuda")
        self.assertEqual(gpu["indice"], "https://download.pytorch.org/whl/cu128")
        self.assertEqual(gpu["paquetes"], ["torch==2.8.0+cu128", "torchaudio==2.8.0+cu128",
                                           "torchvision==0.23.0+cu128"])
        self.assertEqual(cpu["variante"], "cpu")
        self.assertEqual(cpu["indice"], "https://download.pytorch.org/whl/cpu")
        self.assertEqual(cpu["paquetes"], ["torch==2.8.0+cpu", "torchaudio==2.8.0+cpu", "torchvision==0.23.0+cpu"])
        self.assertIsNone(self.wx.plan("no_apto"))
        self.assertIsNone(self.wx.plan("otro"))

    def test_commands_install_torch_first_then_pinned_whisperx(self):
        torch, whisperx = self.wx.commands(self.python, "solo_cpu")
        self.assertEqual(torch[:4], [str(self.python), "-m", "pip", "install"])
        self.assertEqual(torch[torch.index("--index-url") + 1], "https://download.pytorch.org/whl/cpu")
        self.assertIn("torch==2.8.0+cpu", torch)
        self.assertEqual(whisperx[-1], "whisperx==3.8.6")
        self.assertNotIn("--index-url", whisperx)
        with self.assertRaises(ValueError):
            self.wx.commands(self.python, "no_apto")

    def test_no_apto_never_starts(self):
        popen = MagicMock()
        with self.assertRaises(ValueError):
            self.wx.start(self.runtime, "no_apto", python=self.python, popen=popen)
        popen.assert_not_called()
        self.assertFalse((self.runtime / "procesamiento").exists())
        self.assertEqual(self.wx.status(self.runtime)["estado"], "sin_iniciar")

    def test_start_launches_a_detached_runner_and_records_state(self):
        popen = MagicMock()
        state = self.wx.start(self.runtime, "apto_gpu", python=self.python, popen=popen)
        self.assertEqual(state["estado"], "instalando")
        self.assertEqual(state["variante"], "cuda")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd, [str(self.python), "-m", "academic_setup.whisperx", "--runtime",
                               str(self.runtime), "--veredicto", "apto_gpu"])
        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs.get("start_new_session") or kwargs.get("creationflags"))
        self.assertEqual(self.wx.status(self.runtime)["estado"], "instalando")

    def test_start_while_running_does_not_launch_twice(self):
        from capture.storage.job import job_lock
        popen = MagicMock()
        self.wx.start(self.runtime, "solo_cpu", python=self.python, popen=popen)
        with job_lock(self.wx.folder(self.runtime)):
            self.wx.start(self.runtime, "solo_cpu", python=self.python, popen=popen)
        self.assertEqual(popen.call_count, 1)

    def test_launch_failure_is_recorded(self):
        popen = MagicMock(side_effect=OSError("no"))
        with self.assertRaises(OSError):
            self.wx.start(self.runtime, "solo_cpu", python=self.python, popen=popen)
        self.assertEqual(self.wx.status(self.runtime)["estado"], "fallo")

    def test_stale_running_state_becomes_interrupted_and_can_restart(self):
        popen = MagicMock()
        self.wx.start(self.runtime, "solo_cpu", python=self.python, popen=popen, now=lambda: 1000.0)
        with patch.object(self.wx.time, "time", return_value=1000.0 + self.wx.STARTING_GRACE_SECONDS + 1):
            self.assertEqual(self.wx.status(self.runtime)["estado"], "interrumpida")
            self.wx.start(self.runtime, "solo_cpu", python=self.python, popen=popen)
        self.assertEqual(popen.call_count, 2)

    def test_run_executes_both_steps_logs_and_marks_installed(self):
        runner = MagicMock(return_value=subprocess.CompletedProcess([], 0))
        self.enterContext(patch.object(self.wx, "install_ffmpeg"))
        self.assertEqual(self.wx.run(self.runtime, "apto_gpu", python=self.python, runner=runner), 0)
        self.assertEqual([c.args[0] for c in runner.call_args_list], self.wx.commands(self.python, "apto_gpu"))
        self.assertEqual(self.wx.status(self.runtime)["estado"], "instalado")
        self.assertIn("whisperx==3.8.6", self.wx.log_file(self.runtime).read_text(encoding="utf-8"))

    def test_run_failure_stops_and_can_be_retried(self):
        runner = MagicMock(return_value=subprocess.CompletedProcess([], 1))
        self.enterContext(patch.object(self.wx, "install_ffmpeg"))
        self.assertEqual(self.wx.run(self.runtime, "solo_cpu", python=self.python, runner=runner), 1)
        self.assertEqual(runner.call_count, 1)  # sin PyTorch no se intenta WhisperX
        state = self.wx.status(self.runtime)
        self.assertEqual((state["estado"], state["codigo"]), ("fallo", 1))
        runner.return_value = subprocess.CompletedProcess([], 0)
        self.assertEqual(self.wx.run(self.runtime, "solo_cpu", python=self.python, runner=runner), 0)
        self.assertEqual(self.wx.status(self.runtime)["estado"], "instalado")

    def test_main_entry_point_runs_the_install(self):
        with patch.object(self.wx, "run", return_value=0) as run:
            self.assertEqual(self.wx.main(["--runtime", str(self.runtime), "--veredicto", "solo_cpu"]), 0)
        run.assert_called_once_with(Path(str(self.runtime)), "solo_cpu")
        with patch.object(self.wx, "run", return_value=1):
            self.assertEqual(self.wx.main(["--runtime", str(self.runtime), "--veredicto", "solo_cpu"]), 1)

    def test_console_python_replaces_pythonw(self):
        folder = Path(self.tmp.name) / "py"
        folder.mkdir()
        (folder / "python.exe").write_bytes(b"")
        self.assertEqual(self.wx.console_python(folder / "pythonw.exe"), folder / "python.exe")
        self.assertEqual(self.wx.console_python(folder / "python.exe"), folder / "python.exe")

    def test_worker_waits_for_a_lock_held_briefly_by_the_status_probe(self):
        """A1: el sondeo de estado del asistente toma el bloqueo un instante; el proceso no se rinde."""
        from capture.models import CaptureError
        from capture.storage import job
        real, calls = job.job_lock, []

        def flaky(directory):
            calls.append(directory)
            if len(calls) == 1:
                raise CaptureError("El job ya está siendo procesado por otra sesión.")
            return real(directory)
        runner = MagicMock(return_value=subprocess.CompletedProcess([], 0))
        with patch.object(job, "job_lock", flaky), patch.object(self.wx.time, "sleep") as sleep, \
                patch.object(self.wx, "install_ffmpeg"):
            self.assertEqual(self.wx.run(self.runtime, "solo_cpu", python=self.python, runner=runner), 0)
        sleep.assert_called()
        self.assertEqual(self.wx.status(self.runtime)["estado"], "instalado")

    def test_unexpected_error_in_the_worker_is_recorded_as_a_plain_failure(self):
        # El proceso de fondo no tiene consola: un error inesperado debe quedar en el estado, no perderse.
        runner = MagicMock(side_effect=RuntimeError("inesperado"))
        with self.assertRaises(RuntimeError):
            self.wx.run(self.runtime, "solo_cpu", python=self.python, runner=runner)
        state = self.wx.status(self.runtime)
        self.assertEqual(state["estado"], "fallo")
        self.assertIn("error inesperado", state["motivo"])
        self.assertNotIn("inesperado)", state["motivo"])  # solo el tipo, nunca el texto interno
        with patch.object(self.wx, "run", side_effect=RuntimeError("x")):
            self.assertEqual(self.wx.main(["--runtime", str(self.runtime), "--veredicto", "solo_cpu"]), 1)

    def test_ffmpeg_pin_is_exact_and_verified(self):
        pin = self.wx.FFMPEG
        self.assertEqual(pin["version"], "9.0.2")
        self.assertEqual(pin["url"], "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
                                     "ffmpeg-9.0.2-essentials_build.zip")
        self.assertEqual(pin["sha256"], "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba")

    def fake_ffmpeg(self):
        import hashlib
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe", b"FFMPEG")
            archive.writestr("ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe", b"FFPROBE")
            archive.writestr("ffmpeg-9.0.2-essentials_build/bin/ffplay.exe", b"FFPLAY")
        data = buffer.getvalue()
        return data, {**self.wx.FFMPEG, "sha256": hashlib.sha256(data).hexdigest()}

    def test_ffmpeg_is_downloaded_verified_and_placed_next_to_python(self):
        data, pin = self.fake_ffmpeg()
        target, cache = Path(self.tmp.name) / "python", Path(self.tmp.name) / "cache"
        fetch = MagicMock(side_effect=lambda url, path: Path(path).write_bytes(data))
        with patch.object(self.wx, "FFMPEG", pin):
            self.wx.install_ffmpeg(target, cache, fetch=fetch)
            self.assertEqual((target / "ffmpeg.exe").read_bytes(), b"FFMPEG")
            self.assertEqual((target / "ffprobe.exe").read_bytes(), b"FFPROBE")
            self.assertFalse((target / "ffplay.exe").exists())
            self.wx.install_ffmpeg(target, cache, fetch=fetch)  # ya instalado: no descarga otra vez
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], pin["url"])

    def test_ffmpeg_with_a_different_hash_fails_closed(self):
        data, pin = self.fake_ffmpeg()
        target, cache = Path(self.tmp.name) / "python", Path(self.tmp.name) / "cache"
        fetch = MagicMock(side_effect=lambda url, path: Path(path).write_bytes(data + b"x"))
        with patch.object(self.wx, "FFMPEG", pin), self.assertRaises(self.wx.InstallError):
            self.wx.install_ffmpeg(target, cache, fetch=fetch)
        self.assertFalse((target / "ffmpeg.exe").exists())
        self.assertFalse([p for p in cache.iterdir() if p.suffix in (".zip", ".part")])

    def test_ffmpeg_without_a_verified_hash_never_downloads(self):
        fetch = MagicMock()
        with patch.object(self.wx, "FFMPEG", {**self.wx.FFMPEG, "sha256": "PENDIENTE"}), \
                self.assertRaises(self.wx.InstallError):
            self.wx.install_ffmpeg(Path(self.tmp.name) / "python", Path(self.tmp.name) / "cache", fetch=fetch)
        fetch.assert_not_called()

    def test_run_installs_ffmpeg_after_whisperx_and_records_its_failure(self):
        runner = MagicMock(return_value=subprocess.CompletedProcess([], 0))
        with patch.object(self.wx, "install_ffmpeg") as ffmpeg:
            self.assertEqual(self.wx.run(self.runtime, "solo_cpu", python=self.python, runner=runner), 0)
        ffmpeg.assert_called_once_with(self.python.parent, self.wx.folder(self.runtime))
        with patch.object(self.wx, "install_ffmpeg", side_effect=self.wx.InstallError("hash distinto")):
            self.assertNotEqual(self.wx.run(self.runtime, "solo_cpu", python=self.python, runner=runner), 0)
        self.assertEqual(self.wx.status(self.runtime)["estado"], "fallo")
        self.assertIn("hash distinto", self.wx.log_file(self.runtime).read_text(encoding="utf-8"))

    def test_ffmpeg_next_to_python_is_detected_on_windows(self):
        from academic_setup import hardware
        folder = Path(self.tmp.name) / "py"
        folder.mkdir()
        with patch.object(hardware.shutil, "which", return_value=None):
            self.assertFalse(hardware._ffmpeg_found(windows=True, executable=folder / "python.exe"))
            (folder / "ffmpeg.exe").write_bytes(b"")
            self.assertTrue(hardware._ffmpeg_found(windows=True, executable=folder / "python.exe"))
            self.assertFalse(hardware._ffmpeg_found(windows=False, executable=folder / "python.exe"))

    def test_check_cli_is_unchanged(self):
        """Regresión: el CLI de la comprobación sigue con su única acción."""
        from academic_setup.__main__ import parser
        actions = [a for a in parser()._actions if a.dest == "action"]
        self.assertEqual(actions[0].choices, ("check",))


if __name__ == "__main__":
    unittest.main()
