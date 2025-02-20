from rest_framework import serializers
from oscar.core.loading import get_model
UserAddress = get_model('address', 'UserAddress')

class UserAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAddress
        fields = [
            'id', 'title', 'first_name', 'last_name', 'line1', 'line2', 'line3',
            'line4', 'state', 'postcode', 'phone_number', 'is_default_for_shipping',
            'is_default_for_billing', 'notes'
        ]
        read_only_fields = ['id']