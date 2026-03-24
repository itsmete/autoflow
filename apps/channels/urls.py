from django.urls import path
from .views import ChannelListCreateView,ChannelDetailView


urlpatterns = [
        path('',ChannelListCreateView.as_view()),
        path('<uuid:pk>/',ChannelDetailView.as_view())
]