# pylint: disable=unbalanced-tuple-unpacking
from rest_framework import generics
from rest_framework.response import Response
from django.db.models import Q
from django.db.models import F

from oscar.core.loading import get_class, get_model
from collections import defaultdict

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

        queryset = (
            find_from_full_slug(breadcrumb_path, "/").get_children()
            if breadcrumb_path else
            Category.get_root_nodes()
        ).filter(vendor=vendor).distinct()

        # Prefetch all public, in-stock products related to these categories
        products = (
            Product.objects
            .filter(
                is_public=True,
                stockrecords__branch_id=branch_id,
                stockrecords__num_in_stock__gt=F("stockrecords__num_allocated"),
                categories__in=queryset,
            )
            .select_related()
            .distinct()
        )

        if search_query:
            products = products.filter(
                Q(title__icontains=search_query) |
                Q(description__icontains=search_query)
            )
        
        # ── map products → category id ─────────────────────────────────────────
        cat_products = defaultdict(list)
        for product in products:
            for cat in product.categories.all():
                cat_products[cat.id].append(product)

        # ── keep ONLY the categories that actually have products ───────────────
        ids_with_products = [cid for cid, prods in cat_products.items() if prods]

        if not ids_with_products:
            return queryset.none()      # nothing to show

        queryset = queryset.filter(id__in=ids_with_products)

        # attach the pre‑filtered products to each remaining category
        for cat in queryset:
            cat.filtered_products = cat_products.get(cat.id, [])

        return queryset


class CategoryDetail(generics.RetrieveAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
