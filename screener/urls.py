from django.urls import path
from .views import screener_view, importar_view, stock_detail_view

urlpatterns = [
    path('', screener_view, name='screener'),
    path('importar/', importar_view, name='importar'),
    path('stock/<str:ticker>/', stock_detail_view, name='stock_detail'),
]