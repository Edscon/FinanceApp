from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('datos/', include('market.urls')),
    path('', include('backtests.urls')),
    path('', include('core.urls')),
]
