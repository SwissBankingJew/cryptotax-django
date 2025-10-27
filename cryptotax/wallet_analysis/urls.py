"""
URL configuration for wallet_analysis app.
"""

from django.urls import path
from . import views

app_name = 'wallet_analysis'

urlpatterns = [
    # Dashboard
    path('dashboard/', views.dashboard_view, name='dashboard'),

    # Order creation
    path('analysis/new/', views.create_order_view, name='create_order'),

    # Payment
    path('analysis/order/<uuid:order_id>/payment/', views.payment_page_view, name='payment_page'),
    path('order/<uuid:order_id>/verify/', views.verify_signature_view, name='verify_signature'),

    # Order detail
    path('analysis/order/<uuid:order_id>/', views.order_detail_view, name='order_detail'),

    # Report download
    path('reports/<uuid:report_id>/download/', views.download_report_view, name='download_report'),

    # API endpoints
    path('api/payment-verify/', views.verify_payment_api, name='verify_payment'),
    path('api/payment-status/<uuid:order_id>/', views.payment_status_api, name='payment_status'),
    path('api/solana-rpc/', views.solana_rpc_proxy, name='solana_rpc_proxy'),
]
