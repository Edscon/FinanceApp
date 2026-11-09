from django.urls import path

from . import views

app_name = 'backtests'

urlpatterns = [
    path('backtests/', views.runs_list, name='list'),
    path('backtests/nuevo/', views.new, name='new'),
    path('backtests/comparar/', views.compare, name='compare'),
    path('backtests/<int:pk>/', views.detail, name='detail'),
    path('backtests/<int:pk>/robustez/', views.robustness, name='robustness'),
    path('backtests/<int:pk>/borrar/', views.delete, name='delete'),
    path('backtests/<int:pk>/operaciones.csv', views.trades_csv, name='trades_csv'),
    path('backtests/<int:pk>/equity.csv', views.equity_csv, name='equity_csv'),
    path('escaner/', views.scanner, name='scanner'),
    path('escaner/par/', views.pair, name='pair'),
]
