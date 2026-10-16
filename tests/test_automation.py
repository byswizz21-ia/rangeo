"""Ligne de commande, tâches planifiées, disques, menu contextuel, YAML."""

import datetime as dt
import os
import winreg
import xml.dom.minidom

import pytest

import dataset
from rangeo.core import devices, scheduler, winintegration


def test_cli_sort_dry_run_then_real_then_undo(home, tmp_path, capsys):
    from rangeo import cli

    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "facture.pdf").write_bytes(dataset.pdf_bytes())
    (src / "photo.jpg").write_bytes(dataset.jpeg_bytes(date="2022:01:02 10:00:00"))
    assert cli.main(["sort", str(src), "--dest", str(dst), "--dry-run"]) == 0
    assert (src / "facture.pdf").exists()
    assert "2 élément(s)" in capsys.readouterr().out
    assert cli.main(["sort", str(src), "--dest", str(dst), "--yes"]) == 0
    assert not (src / "facture.pdf").exists()
    out = capsys.readouterr().out
    session = int(out.split("Session #")[1].split(" ")[0])
    assert cli.main(["history"]) == 0
    assert cli.main(["undo", str(session), "--yes"]) == 0
    assert (src / "facture.pdf").exists() and (src / "photo.jpg").exists()


def test_cli_duplicates(home, tmp_path, capsys):
    from rangeo import cli

    d = tmp_path / "d"
    d.mkdir()
    (d / "a.bin").write_bytes(b"x" * 5000)
    (d / "a (1).bin").write_bytes(b"x" * 5000)
    assert cli.main(["duplicates", str(d), "--dry-run"]) == 0
    assert "1 groupe(s)" in capsys.readouterr().out


def test_schedule_xml_and_next_run():
    job = scheduler.new_job("Téléchargements & co", [r"C:\x"], "general")
    job.update(frequency="weekly", days=["MON", "FRI"], time="21:30")
    doc = xml.dom.minidom.parseString(scheduler.task_xml(job).replace('encoding="UTF-16"', ""))
    assert doc.getElementsByTagName("StartWhenAvailable")[0].firstChild.data == "true"
    assert doc.getElementsByTagName("Monday") and doc.getElementsByTagName("Friday")
    assert "run-job" in doc.getElementsByTagName("Arguments")[0].firstChild.data
    now = dt.datetime(2026, 10, 14, 22, 0)  # mercredi
    assert scheduler.next_run(job, now) == dt.datetime(2026, 10, 16, 21, 30)  # vendredi
    job.update(frequency="monthly", day=1, time="08:00")
    assert scheduler.next_run(job, now) == dt.datetime(2026, 11, 1, 8, 0)


def test_schedule_real_task_roundtrip():
    job = scheduler.new_job("test automatique", [r"C:\x"], "general")
    job["id"] = "pytest" + job["id"][:4]
    try:
        scheduler.install(job)
        assert scheduler.exists(job["id"])
    finally:
        scheduler.remove(job["id"])
    assert not scheduler.exists(job["id"])


def test_run_job(engine, tmp_path):
    src = tmp_path / "Telechargements"
    src.mkdir()
    (src / "doc.pdf").write_bytes(dataset.pdf_bytes())
    job = scheduler.new_job("soir", [str(src)], "downloads")
    engine.config.set("schedules", [job])
    result = scheduler.run_job(engine, job)
    assert result is not None and result.files == 1
    assert engine.config.get("schedules")[0]["last_session"] == result.session_id


def test_volumes_listed():
    vols = devices.volumes()
    assert any(v.root.upper().startswith(os.environ.get("SystemDrive", "C:").upper()) for v in vols)
    monitor = devices.DeviceMonitor()
    assert monitor.poll() == []
    rule = {"serial": vols[0].serial, "action": "ask"}
    assert devices.find_rule([rule], vols[0]) is rule


def test_context_menu_registry_in_test_location():
    base = r"Software\RangeoTests\Classes"
    try:
        winintegration.register_context_menu("Ranger avec Rangeo", base)
        assert winintegration.context_menu_registered(base)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Directory\shell\Rangeo\command") as key:
            cmd = winreg.QueryValueEx(key, "")[0]
        assert "--open-sort" in cmd and '"%1"' in cmd
    finally:
        winintegration.unregister_context_menu(base)
        for path in (base + r"\Directory\Background\shell", base + r"\Directory\Background", base + r"\Directory\shell",
                     base + r"\Directory", base + r"\Drive\shell", base + r"\Drive", base, r"Software\RangeoTests"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass
    assert not winintegration.context_menu_registered(base)


@pytest.mark.parametrize("ext", ["json", "yaml"])
def test_profile_export_import(engine, tmp_path, ext):
    cfg = engine.config
    path = str(tmp_path / f"profil.{ext}")
    profile = cfg.profile("general")
    cfg.export_profile(profile, path)
    imported = cfg.import_profile(path)
    assert imported["name"] == profile["name"]
    assert len(imported["rules"]) == len(profile["rules"])
    cfg.export_all(str(tmp_path / f"tout.{ext}"))
    cfg.import_all(str(tmp_path / f"tout.{ext}"))


def test_device_monitor_is_light(monkeypatch):
    """Disques déjà connus non réinterrogés, lecteurs réseau ignorés, carte mémoire changée détectée."""
    queried = []

    class FakeKernel:
        mask = 0b100  # C:
        types = {"C:\\": 3}

        def GetLogicalDrives(self):
            return self.mask

        def SetErrorMode(self, mode):
            return 0

        def GetDriveTypeW(self, root):
            return self.types.get(root, 1)

    k = FakeKernel()
    media = {"C:\\": "0000000C"}

    def info(root, dtype):
        queried.append(root)
        return devices.Volume(root, "", media[root], dtype) if root in media else None

    monkeypatch.setattr(devices, "_k32", k)
    monkeypatch.setattr(devices, "_volume_info", info)
    mon = devices.DeviceMonitor()
    assert queried == ["C:\\"]
    queried.clear()
    assert mon.poll() == [] and queried == []  # disque fixe connu : pas réinterrogé
    k.mask |= 1 << 4  # E: clé USB
    k.types["E:\\"] = 2
    media["E:\\"] = "0000000E"
    assert [v.root for v in mon.poll()] == ["E:\\"]
    assert mon.poll() == []  # toujours la même clé
    media["E:\\"] = "000000EE"  # autre carte dans le même lecteur
    assert [v.serial for v in mon.poll()] == ["000000EE"]
    del media["E:\\"]  # carte retirée puis remise
    assert mon.poll() == []
    media["E:\\"] = "000000EE"
    assert [v.serial for v in mon.poll()] == ["000000EE"]
    k.mask |= 1 << 25  # Z: lecteur réseau : jamais interrogé
    k.types["Z:\\"] = 4
    queried.clear()
    assert mon.poll() == [] and "Z:\\" not in queried
    k.mask &= ~(1 << 4)  # E: débranchée
    assert mon.poll() == []
    k.mask |= 1 << 4  # rebranchée
    assert [v.root for v in mon.poll()] == ["E:\\"]
