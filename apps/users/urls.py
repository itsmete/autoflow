from django.urls import path
from .views import LoginView,RegisterView,\
UserListView,UserDetailView,UserDeleteView,\
UserAdminUpdateView,UserSelfUpdateView

urlpatterns = [
        path('login/',LoginView.as_view()),
        path('register/',RegisterView.as_view()),

        path('',UserListView.as_view()),
        path('me/',UserSelfUpdateView.as_view()),
        path('<uuid:pk>/',UserDetailView.as_view()),
        path('<uuid:pk>/update/',UserAdminUpdateView.as_view()),
        path('<uuid:pk>/delete/',UserDeleteView.as_view()),
        

]