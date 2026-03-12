from django.db import models
from core.models import BaseModel       
from django.contrib.auth.models import BaseUserManager ,AbstractBaseUser,PermissionsMixin


class UserManager(BaseUserManager):
        # django's default user manager works with username as primary, we work with emails

        def create_user(self,email,password=None,**extra_fields):
                if not email:
                        raise ValueError('Email is oblugatoru')

                email = self.normalize_email(email)
                user = self.model(email=email,**extra_fields)

                user.set_password(password)
                user.save(using=self._db)

                return user
        
        def create_superuser(self,email,password=None,**extra_fields):

                extra_fields.setdefault('is_staff',True)
                extra_fields.setdefault('is_superuser',True)
                extra_fields.setdefault('role','super_admin')

                return self.create_user(email,password,**extra_fields)
        


class User(AbstractBaseUser,PermissionsMixin,BaseModel):

        """
                AbsBaseUser : password, last_login etc
                PermissionMixin : groups , permissions etc
                BaseModel : id (uuid), created_at ,updated_At is_active
        
        """


        class Role(models.TextChoices):
                SUPER_ADMIN = 'super_admin', 'Super_Admin'
                OWNER = 'owner' , 'Branch Owner'
                BRANCH_MANAGER = 'branch_manager' , 'Branch Manager'
                STAFF = 'staff' , 'Staff'


        email = models.EmailField(unique=True)
        first_name = models.CharField(max_length=100)
        last_name = models.CharField(max_length=100)

        role = models.CharField(
                max_length=20,
                choices=Role.choices,
                default=Role.STAFF
        )



        tenant = models.ForeignKey(
                'tenants.Tenant',
                on_delete=models.CASCADE,
                null=True,
                blank=True,
                related_name = 'users'
        )


        branch = models.ForeignKey(
                'tenants.Branch',
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name = 'users'
        )


        is_staff = models.BooleanField(default=False) # acces to admin panel

        USERNAME_FIELD = 'email'
        REQUIRED_FIELDS = ['first_name','last_name']

        objects = UserManager() # connecting the manager


        def __str__(self):
                return f" {self.get_full_name()} <{self.email}> "
        
        def get_full_name(self):
                return f"{self.first_name} - {self.last_name}"
        

        @property
        def is_owner(self):
                return self.role == self.Role.OWNER
                # LET US SAY if user.is_owner instead of if (user.role == bla)
        
        @property
        def is_super_admin(self):
                return self.role == self.Role.SUPER_ADMIN

        class Meta:
                db_table = 'users'