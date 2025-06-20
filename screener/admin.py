from django.contrib import admin
from .models import Stock, Fundamental

admin.site.register(Stock)
admin.site.register(Fundamental)