from rest_framework import serializers
from .models import Tenant, Branch

class TenantSerializer(serializers.ModelSerializer):
        class Meta:
                model = Tenant

                fields = ['id','name','slug','created_at']
                read_only_fields= ['id','slug','created_at']
                # we made slug field read only because it will automatically created.
                # but we can save override and custom slug via slufigy()

class BranchSerializer(serializers.ModelSerializer):
        class Meta:
                model = Branch
                fields = ['id','name','address','phone','is_hq','created_at','tenant']
                read_only_fields = ['id','created_at','tenant']

"""
        Our branch serializer dont have a tenant field, which is super crucial.
        But adding a tenant directly may cause some security issues (user can give another tenants id etc)

        However , our lifesafer serializer.save() method accepts argument that is not in the serializer but in the model


        serializer.save(tenant = request.user.tenant)

"""


       
        
        



