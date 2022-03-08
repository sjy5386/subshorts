from django.urls import path

from . import views

urlpatterns = [
    path('', views.list_records, name='record_list'),
    path('create/', views.create_record, name='record_create'),
    path('<str:identifier>/', views.retrieve_record, name='record_detail'),
    path('<str:identifier>/update/', views.update_record, name='record_update'),
    path('<str:identifier>/delete/', views.delete_record, name='record_delete'),
]
