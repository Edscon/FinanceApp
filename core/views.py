from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Job


def dashboard(request):
    # Se amplía con datos y backtests desde market/backtests (ver dashboard_context()).
    from .dashboard import dashboard_context

    return render(request, 'core/dashboard.html', dashboard_context())


def jobs_list(request):
    return render(request, 'core/jobs.html', {'jobs': Job.objects.all()[:100]})


def job_status(request, pk):
    """Parcial HTMX. Mientras el trabajo está activo se vuelve a pedir cada segundo; al terminar
    responde 286 (HTMX deja de sondear) o, con ?reload=1, pide recargar la página completa."""
    job = get_object_or_404(Job, pk=pk)
    response = render(request, 'core/_job_status.html', {'job': job, 'reload': request.GET.get('reload')})
    if not job.is_active:
        if request.GET.get('reload'):
            response['HX-Refresh'] = 'true'
        else:
            response.status_code = 286
    return response


@require_POST
def job_cancel(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if job.status == Job.Status.QUEUED:
        Job.objects.filter(pk=pk, status=Job.Status.QUEUED).update(status=Job.Status.CANCELLED)
    elif job.status == Job.Status.RUNNING:
        Job.objects.filter(pk=pk).update(cancel_requested=True)
    messages.info(request, 'Se ha pedido cancelar el trabajo.')
    if request.headers.get('HX-Request'):
        return HttpResponse(status=204)
    return redirect(request.POST.get('next') or 'core:jobs')
