from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('trabajos/', views.jobs_list, name='jobs'),
    path('trabajos/<int:pk>/estado/', views.job_status, name='job_status'),
    path('trabajos/<int:pk>/cancelar/', views.job_cancel, name='job_cancel'),
]
