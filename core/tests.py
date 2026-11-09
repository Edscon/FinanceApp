import datetime as dt
import time

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import jobs
from core.models import Job
from core.templatetags import fmt


@jobs.register('test_ok')
def _ok(job, reporter):
    reporter.progress(0.5, 'a mitad')
    return {'message': 'hecho', 'value': float('nan')}


@jobs.register('test_fail')
def _fail(job, reporter):
    raise RuntimeError('fallo provocado')


@jobs.register('test_cancel')
def _cancel(job, reporter):
    Job.objects.filter(pk=job.pk).update(cancel_requested=True)
    reporter._last_write = 0
    reporter.progress(0.9, 'debería cancelarse')
    return {'message': 'no debería llegar'}


@override_settings(JOBS_MODE='sync')
class JobLifecycleTests(TestCase):
    def test_success_stores_a_json_safe_result(self):
        job = jobs.enqueue('test_ok', {'x': 1}, label='prueba')
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.SUCCEEDED)
        self.assertEqual(job.result, {'message': 'hecho', 'value': None})
        self.assertEqual(job.message, 'hecho')
        self.assertEqual(job.progress, 1.0)

    def test_failure_keeps_the_traceback(self):
        with self.assertLogs('core.jobs', 'ERROR'):
            job = jobs.enqueue('test_fail')
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn('fallo provocado', job.error)

    def test_cancellation(self):
        job = jobs.enqueue('test_cancel')
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.CANCELLED)

    def test_unknown_kind_fails_cleanly(self):
        with self.assertLogs('core.jobs', 'ERROR'):
            job = jobs.enqueue('no_existe')
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn('desconocido', job.error)


@override_settings(JOBS_MODE='external')
class QueueTests(TestCase):
    def test_claim_is_atomic(self):
        job = jobs.enqueue('test_ok')
        self.assertEqual(jobs.claim_next('w1').pk, job.pk)
        self.assertIsNone(jobs.claim_next('w2'))

    def test_stale_running_jobs_are_reaped(self):
        job = Job.objects.create(kind='test_ok', status=Job.Status.RUNNING,
                                 heartbeat_at=timezone.now() - dt.timedelta(minutes=10))
        fresh = Job.objects.create(kind='test_ok', status=Job.Status.RUNNING, heartbeat_at=timezone.now())
        self.assertEqual(jobs.reap_stale(), 1)
        job.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertEqual(fresh.status, Job.Status.RUNNING)

    def test_external_mode_leaves_the_job_queued_for_run_worker(self):
        job = jobs.enqueue('test_ok')
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertEqual(jobs.drain(once=True), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.SUCCEEDED)


class JobViewTests(TestCase):
    def test_status_partial_polls_while_active_and_stops_when_done(self):
        job = Job.objects.create(kind='test_ok', status=Job.Status.RUNNING, progress=0.4)
        response = self.client.get(reverse('core:job_status', args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-trigger="every 1s"')
        Job.objects.filter(pk=job.pk).update(status=Job.Status.SUCCEEDED)
        self.assertEqual(self.client.get(reverse('core:job_status', args=[job.pk])).status_code, 286)
        response = self.client.get(reverse('core:job_status', args=[job.pk]) + '?reload=1')
        self.assertEqual(response['HX-Refresh'], 'true')

    def test_cancel_a_queued_job(self):
        job = Job.objects.create(kind='test_ok')
        self.client.post(reverse('core:job_cancel', args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.CANCELLED)

    def test_pages(self):
        for name in ('core:dashboard', 'core:jobs'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)


@override_settings(JOBS_MODE='thread')
class ThreadModeTests(TransactionTestCase):
    def test_the_worker_thread_runs_the_job(self):
        job = jobs.enqueue('test_ok')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job.refresh_from_db()
            if not job.is_active:
                break
            time.sleep(0.05)
        self.assertEqual(job.status, Job.Status.SUCCEEDED)


class FormatTests(TestCase):
    def test_spanish_number_formats(self):
        self.assertEqual(fmt.num(1234.567), '1.234,57')
        self.assertEqual(fmt.pct(0.1234), '12,3 %')
        self.assertEqual(fmt.signed_pct(0.05), '+5,0 %')
        self.assertEqual(fmt.money(100000), '100.000 $')
        self.assertEqual(fmt.num(None), '—')
        self.assertEqual(fmt.num(float('nan')), '—')
        self.assertEqual((fmt.tone(1), fmt.tone(-1), fmt.tone(0)), ('pos', 'neg', ''))
        self.assertEqual(fmt.at(['a', 'b'], 3), 'b')
