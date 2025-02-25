from rest_framework import serializers
from oscar.core.loading import get_model
UserAddress = get_model('address', 'UserAddress')

class UserAddressSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserAddress
        fields = [
            'id', 'address_name','line1', 'latitude', 'longitude','phone_number','is_default_for_shipping' 
        ]