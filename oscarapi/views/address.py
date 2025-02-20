from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from oscar.core.loading import get_model
from oscarapi.utils.loading import get_api_classes

UserAddress = get_model('address', 'UserAddress')
(UserAddressSerializer, ) = get_api_classes(
    "serializers.address",
    ["UserAddressSerializer",],
)

class AddressListView(generics.ListCreateAPIView):
    """
    API to list all addresses or create a new address.
    """
    serializer_class = UserAddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Only return addresses for the logged-in user
        return UserAddress.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # Automatically associate the address with the logged-in user
        serializer.save(user=self.request.user)


class AddressDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    API to fetch, update, or delete a specific address.
    """
    serializer_class = UserAddressSerializer
    permission_classes = [IsAuthenticated]


    def get_queryset(self):
        # Only allow access to addresses of the logged-in user
        return UserAddress.objects.filter(user=self.request.user)

    def perform_destroy(self, instance):
        # Ensure the address belongs to the logged-in user before deleting
        if instance.user == self.request.user:
            instance.delete()
        else:
            return Response(
                {"detail": "You do not have permission to delete this address."},
                status=status.HTTP_403_FORBIDDEN
            )