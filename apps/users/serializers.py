from rest_framework import serializers
from .models import User
from django.contrib.auth import authenticate
from apps.tenants.serializers import TenantSerializer,BranchSerializer
from core.roles import can_manage 
from django.utils.translation import gettext_lazy as _

class UserRegisterSerializer(serializers.ModelSerializer):
        password = serializers.CharField(write_only=True,min_length=8)

        class Meta:
                model = User
                fields = [
                        'email',
                        'password',
                        'first_name',
                        'last_name',
                        'role',
                        'branch',
                ]


        def validate_role(self, value):

                request = self.context.get('request')

                if  request.user.is_super_admin:
                        return value
                
                #owners can only assing branch managers and staff
                if request.user.is_owner:
                        allowed = [User.Role.BRANCH_MANAGER,User.Role.STAFF]

                        if value not in allowed:
                                raise serializers.ValidationError(
                                        _('You are not authorized for assing that role , please switch the super-admin mode')
                                )
                        
                        return value
                
                raise serializers.ValidationError(_('You dont have a permission to create a user'))
        
        def validate_branch(self,value):
                request = self.context.get('request')

                if value and value.tenant != request.user.tenant:
                        raise serializers.ValidationError(
                                _('That branch is not yours')
                        )


                return value


        def create(self, validated_data):
                request = self.context.get('request')

                validated_data['tenant'] = request.user.tenant


                #if super_admin : True else false
                validated_data['is_staff'] = (
                        validated_data.get('role') == User.Role.SUPER_ADMIN
                )

                # password is saved by hashed
                # create() -> no hash create_user() -> calls set_password()
                return User.objects.create_user(**validated_data)
        

class UserLoginSerializer(serializers.Serializer):

        email = serializers.EmailField()
        password = serializers.CharField(write_only = True, style={'input_type':'password'})
        # write_only guarentees that, that value will come in , not out
        
        def validate(self,data):

                user = authenticate(email=data.get('email'),password = data.get('password'))

                if not user:
                        raise serializers.ValidationError(_('Email or Password is incorrect'))
                
                data['user'] = user 

                return data   
        




class BaseUserSerializer(serializers.ModelSerializer):
        tenant = TenantSerializer(read_only =True)
        branch = BranchSerializer(read_only =True)

        class Meta:
                model = User
                fields = ['id', 'email', 'first_name', 'last_name', 'role', 'tenant', 'branch']


class UserProfileSerializer(BaseUserSerializer):

        # nested serializer : we returning tenant data as object, not plane ID 

        class Meta(BaseUserSerializer.Meta): 
                fields = BaseUserSerializer.Meta.fields + ['created_at']
                read_only_fields = fields # you cannot change all of those feilds via this serializer



class UserReaderSerializer(BaseUserSerializer):

        class Meta(BaseUserSerializer.Meta):

                fields = BaseUserSerializer.Meta.fields + ['created_at', 'is_active']
                read_only_fields = fields # only to list , not change
                



class UserSelfUpdateSerializer(BaseUserSerializer):
 
        current_password = serializers.CharField(write_only = True,min_length = 8)
        new_password = serializers.CharField(write_only = True,min_length = 8)
        confirm_password = serializers.CharField(write_only = True,min_length = 8)


        class Meta(BaseUserSerializer.Meta):
                fields= ['current_password','new_password','confirm_password']

                
        
        def validate(self,data):

                request= self.context.get('request')
                user = request.user


                if not user.check_password(data.get('current_password')):
                        raise serializers.ValidationError(_('Wrong Password Entered.'))

                if (data.get('new_password') == data.get('confirm_password')) :
                        return data
                else :
                        raise serializers.ValidationError('Passwords doesnt match')
                


        def update(self,instance,validated_data):
                instance.set_password(validated_data['new_password'])
                instance.save()

                return instance
                


class UserAdminUpdateSerializer(BaseUserSerializer):
        class Meta(BaseUserSerializer.Meta):
                fields = BaseUserSerializer.Meta.fields + ['is_active']
        

        def validate_role(self,value):

                request = self.context.get('request')
                user = request.user
                if (can_manage(str(user.role),str(value))):
                        return value
                else:
                        raise serializers.ValidationError(_('You cannot assign a role equivalent or bigger than yours'))
                

        def validate_branch(self,value):
                
                request= self.context.get('request')
                if request.user.is_super_admin:
                        return value
                
                if value and value.tenant != request.user.tenant:
                        raise serializers.ValidationError(_('That branch is not belongs to you'))

                return value

