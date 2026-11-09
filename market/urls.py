from django.urls import path

from . import views

app_name = 'market'

urlpatterns = [
    path('', views.data, name='data'),
    path('demo/', views.demo, name='demo'),
    path('universos/', views.universes, name='universes'),
    path('universos/predefinidos/', views.presets, name='presets'),
    path('universos/nuevo/', views.universe_new, name='universe_new'),
    path('universos/<int:pk>/', views.universe_detail, name='universe'),
    path('universos/<int:pk>/borrar/', views.universe_delete, name='universe_delete'),
    path('universos/<int:pk>/importar/', views.universe_import, name='universe_import'),
    path('valor/<str:ticker>/', views.instrument, name='instrument'),
]
