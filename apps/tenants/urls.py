from django.urls import path
from .views import TenantDetailView,TenantListCreateView ,\
BranchDetailView , BranchListView



urlpatterns = [
        
        # auth/
        

        # tenants/ 
        path('tenants/',TenantListCreateView.as_view()),
        path('tenants/<uuid:pk>/',TenantDetailView.as_view()),


        # tenants / 
        path('branchs/',BranchListView.as_view()),
        path('branchs/<uuid:pk>/',BranchDetailView.as_view())


]