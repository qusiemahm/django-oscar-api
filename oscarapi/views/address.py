from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from oscar.core.loading import get_model
from oscarapi.utils.loading import get_api_classes
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ParseError
from django.utils.translation import gettext_lazy as _


UserAddress = get_model('address', 'UserAddress')
(UserAddressSerializer, ) = get_api_classes(
    "serializers.address",
    ["UserAddressSerializer",],
)

class UserAddressListCreateView(generics.ListCreateAPIView):
    queryset = UserAddress.objects.all()
    serializer_class = UserAddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Return only addresses for the logged-in user
        return UserAddress.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        validated_data = serializer.validated_data
        address_name = validated_data.get('address_name')

        # Check for duplicate hash
        if UserAddress.objects.filter(user=user, address_name=address_name).exists():
            raise ParseError(_("An address with this name already exists for the user."))

        # Handle default shipping logic
        is_default_for_shipping = validated_data.get('is_default_for_shipping', False)
        if is_default_for_shipping:
            UserAddress.objects.filter(user=user, is_default_for_shipping=True).update(is_default_for_shipping=False)

        # Save the new address
        serializer.save(user=user)
        
    def create(self, request, *args, **kwargs):
        try:
            # Call the parent `create` method, which invokes `perform_create`
            return super().create(request, *args, **kwargs)
        except ParseError as e:
            # Return a 400 Bad Request response with the error details
            return Response( str(e), status=status.HTTP_400_BAD_REQUEST)

class UserAddressDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = UserAddress.objects.all()
    serializer_class = UserAddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Return only addresses for the logged-in user
        return UserAddress.objects.filter(user=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        user = request.user
        is_default_for_shipping = request.data.get('is_default_for_shipping', False)

        if is_default_for_shipping:
            # If this address is to be set as default for shipping, unset the previous default
            UserAddress.objects.filter(user=user, is_default_for_shipping=True).update(is_default_for_shipping=False)

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(serializer.data)
    
    def perform_destroy(self, instance):
        user = self.request.user
        was_default = instance.is_default_for_shipping  # Check if the deleted address was the default

        # Delete the address
        instance.delete()

        if was_default:
            # If the deleted address was the default, set the first available address as the new default
            first_address = UserAddress.objects.filter(user=user).first()
            if first_address:
                first_address.is_default_for_shipping = True
                first_address.save()

class SetDefaultAddressView(generics.UpdateAPIView):
    queryset = UserAddress.objects.all()
    serializer_class = UserAddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def update(self, request, *args, **kwargs):
        address = self.get_object()
        address_type = request.data.get('type')  # 'shipping' or 'billing'

        if address_type == 'shipping':
            # Set this address as the default shipping address
            UserAddress.objects.filter(user=request.user, is_default_for_shipping=True).update(is_default_for_shipping=False)
            address.is_default_for_shipping = True
        elif address_type == 'billing':
            # Set this address as the default billing address
            UserAddress.objects.filter(user=request.user, is_default_for_billing=True).update(is_default_for_billing=False)
            address.is_default_for_billing = True
        else:
            return Response({"error": "Invalid address type"}, status=status.HTTP_400_BAD_REQUEST)

        address.save()
        return Response({"message": f"Default {address_type} address updated successfully."})