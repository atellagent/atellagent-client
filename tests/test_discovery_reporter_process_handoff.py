"""An in-flight device upload excludes another process until lease handoff."""

import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from atellagent_client.onboarding import device_reporter
from atellagent_client.onboarding.reporting_scope import ReportingScope
from atellagent_client.onboarding.reporting_state import DeviceReportingStore


_CHILD = r"""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID
from atellagent_client.onboarding.device_reporter import DeviceReporter
from atellagent_client.onboarding.reporting_scope import ReportingScope
from atellagent_client.onboarding.reporting_state import DeviceReportingStore
scope = ReportingScope(*(UUID(item) for item in sys.argv[1:4]))
store = DeviceReportingStore(Path(sys.argv[4]), scope)
class Client:
    device = scope
    async def status(self):
        return SimpleNamespace(discovery_reporting_status="enabled")
    async def report_discovery(self):
        print("UPLOADING", flush=True)
        await asyncio.to_thread(sys.stdin.readline)
asyncio.run(DeviceReporter(Client(), store).tick())
print("DONE", flush=True)
"""


class DiscoveryReporterProcessHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_upload_hands_off_without_duplicate_report(self):
        with tempfile.TemporaryDirectory() as root:
            scope = ReportingScope(uuid4(), uuid4(), uuid4())
            store = DeviceReportingStore(Path(root).resolve() / "reporting", scope)
            self.assertTrue(store.initialize(reporting_enabled=True))
            child = subprocess.Popen([sys.executable, "-c", _CHILD, str(scope.tenant_id),
                str(scope.device_id), str(scope.owner_user_id), str(store.directory)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            try:
                first = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=5)
                self.assertEqual(first.strip(), "UPLOADING")
                client = SimpleNamespace(device=scope,
                    status=AsyncMock(return_value=SimpleNamespace(discovery_reporting_status="enabled")),
                    report_discovery=AsyncMock())
                competing = device_reporter.DeviceReporter(client, store)
                self.assertFalse(await asyncio.wait_for(competing.tick(), timeout=1))
                client.status.assert_not_awaited()
                client.report_discovery.assert_not_awaited()
                child.stdin.write("\n")
                child.stdin.flush()
                output, error = await asyncio.wait_for(asyncio.to_thread(child.communicate), timeout=5)
                self.assertEqual(child.returncode, 0, error)
                self.assertIn("DONE", output)
                self.assertFalse(await competing.tick())
                with patch.object(device_reporter, "time", return_value=time() + 301):
                    self.assertTrue(await competing.tick())
                client.report_discovery.assert_awaited_once()
            finally:
                if child.poll() is None:
                    child.kill()
                    await asyncio.to_thread(child.communicate)
