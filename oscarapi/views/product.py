# pylint: disable=unbalanced-tuple-unpacking
from rest_framework import generics
from rest_framework.response import Response
from django.db.models import Q
from django.db.models import F

from oscar.core.loading import get_class, get_model

from oscarapi.utils.categories import find_from_full_slug
from oscarapi.utils.loading import get_api_classes, get_api_class
from rest_framework.exceptions import ValidationError
from server.apps.vendor.models import Vendor
Store = get_model('stores', 'store')
Selector = get_class("partner.strategy", "Selector")

(
    CategorySerializer,
    ProductLinkSerializer,
    ProductSerializer,
    ProductStockRecordSerializer,
    AvailabilitySerializer,
) = get_api_classes(
    "serializers.product",
    [
        "CategorySerializer",
        "ProductLinkSerializer",
        "ProductSerializer",
        "ProductStockRecordSerializer",
        "AvailabilitySerializer",
    ],
)

PriceSerializer = get_api_class("serializers.checkout", "PriceSerializer")


__all__ = ("ProductList", "ProductDetail", "ProductPrice", "ProductAvailability")

Product = get_model("catalogue", "Product")
Category = get_model("catalogue", "Category")
StockRecord = get_model("partner", "StockRecord")


class ProductList(generics.ListAPIView):
    serializer_class = ProductSerializer

    def get_queryset(self):
        """
        Filters products based on:
        - branch_id (required)
        - at least one category is_public
        - branch is_active
        - optional structure
        """
        branch_id = self.request.query_params.get("branch_id")
        if not branch_id:
            raise ValidationError({"branch_id": "This parameter is required."})

        qs = Product.objects.filter(
            branches__id=branch_id,
            branches__is_active=True,
            categories__is_public=True,
            is_public=True,
        ).distinct()

        structure = self.request.query_params.get("structure")
        if structure:
            qs = qs.filter(structure=structure)

        category_id = self.request.query_params.get("category_id")
        if category_id:
            qs = qs.filter(categories__id=category_id)
            
        return qs


class ProductDetail(generics.RetrieveAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


class ProductPrice(generics.RetrieveAPIView):
    queryset = Product.objects.all()
    serializer_class = PriceSerializer

    def get(
        self, request, *args, **kwargs
    ):  # pylint: disable=redefined-builtin,arguments-differ
        product = self.get_object()
        strategy = Selector().strategy(request=request, user=request.user)
        ser = PriceSerializer(
            strategy.fetch_for_product(product).price, context={"request": request}
        )
        return Response(ser.data)


class ProductStockRecords(generics.ListAPIView):
    serializer_class = ProductStockRecordSerializer
    queryset = StockRecord.objects.all()

    def get_queryset(self):
        product_pk = self.kwargs.get("pk")
        return super().get_queryset().filter(product_id=product_pk)


class ProductStockRecordDetail(generics.RetrieveAPIView):
    serializer_class = ProductStockRecordSerializer
    queryset = StockRecord.objects.all()


class ProductAvailability(generics.RetrieveAPIView):
    queryset = Product.objects.all()
    serializer_class = AvailabilitySerializer

    def get(
        self, request, *args, **kwargs
    ):  # pylint: disable=redefined-builtin,arguments-differ
        product = self.get_object()
        strategy = Selector().strategy(request=request, user=request.user)
        ser = AvailabilitySerializer(
            strategy.fetch_for_product(product).availability,
            context={"request": request},
        )
        return Response(ser.data)


class CategoryList(generics.ListAPIView):
    serializer_class = CategorySerializer

    def get_queryset(self):
        """
        Fetches the root nodes or children of a category filtered by vendor if provided.
        """
        breadcrumb_path = self.kwargs.get("breadcrumbs", None)
        branch_id = self.request.query_params.get("branch", None)
        search_query = self.request.query_params.get("search", None)

        # Ensure branch_id is provided
        if not branch_id:
            raise ValidationError("branch parameter is required.")

        # Get the store and its vendor
        try:
            store = Store.objects.get(id=branch_id)
            vendor = store.vendor  # Access the related vendor
            
            # Validate store and vendor are active
            if not store.is_active:
                raise ValidationError({"branch": "This store is not active."})
            if not vendor.is_valid:
                raise ValidationError({"branch": "This vendor is not active."})
                
        except Store.DoesNotExist:
            raise ValidationError({"branch": f"No store found with ID {branch_id}."})
        except AttributeError:
            raise ValidationError({"branch": "This store has no associated vendor."})

        # Get root nodes or filter by breadcrumbs
        if breadcrumb_path:
            category = find_from_full_slug(breadcrumb_path, separator="/")
            queryset = category.get_children()
        else:
            queryset = Category.get_root_nodes()

        # Filter by vendor
        queryset = queryset.filter(vendor=vendor)

        # Filter to include only categories that have at least one matching product
        queryset = queryset.filter(
            product__is_public=True,
            product__stockrecords__branch_id=branch_id,
            product__stockrecords__num_in_stock__gt=F("product__stockrecords__num_allocated")
        )

        if search_query:
            queryset = queryset.filter(
                Q(product__title__icontains=search_query) |
                Q(product__description__icontains=search_query)
            )

        return queryset


class CategoryDetail(generics.RetrieveAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
