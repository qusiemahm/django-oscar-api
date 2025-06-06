# pylint: disable=W0632

import logging
from copy import deepcopy
from django.db.models.manager import Manager
from django.utils.translation import gettext as _

from rest_framework import serializers
from rest_framework.fields import empty

from oscar.core.loading import get_model
from oscarapi.basket import operations
from django.db.models import F

from oscarapi.utils.exists import bound_unique_together_get_or_create_multiple
from oscarapi.utils.loading import get_api_classes
from oscarapi import settings
from oscarapi.utils.files import file_hash
from oscarapi.utils.exists import find_existing_attribute_option_group
from oscarapi.utils.accessors import getitems
from oscarapi.serializers.fields import DrillDownHyperlinkedIdentityField
from oscarapi.utils.attributes import AttributeConverter
from oscarapi.serializers.utils import (
    OscarModelSerializer,
    OscarHyperlinkedModelSerializer,
    UpdateListSerializer,
    UpdateForwardManyToManySerializer,
)
from server.apps.service.models import Service
from server.apps.vendor.models import Vendor

from .exceptions import FieldError

logger = logging.getLogger(__name__)
Product = get_model("catalogue", "Product")
Range = get_model("offer", "Range")
ProductAttributeValue = get_model("catalogue", "ProductAttributeValue")
ProductImage = get_model("catalogue", "ProductImage")
Option = get_model("catalogue", "Option")
Partner = get_model("partner", "Partner")
StockRecord = get_model("partner", "StockRecord")
ProductClass = get_model("catalogue", "ProductClass")
ProductAttribute = get_model("catalogue", "ProductAttribute")
Category = get_model("catalogue", "Category")
AttributeOption = get_model("catalogue", "AttributeOption")
AttributeOptionGroup = get_model("catalogue", "AttributeOptionGroup")
AttributeValueField, CategoryField, SingleValueSlugRelatedField = get_api_classes(
    "serializers.fields",
    ["AttributeValueField", "CategoryField", "SingleValueSlugRelatedField"],
)





class ServiceSerializer(OscarModelSerializer):
    """
    Serializer for the Service model.
    """
    
    available_time_slots = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Service
        fields = [
            "id",
            "product",
            "branch",
            "service_type",
            "provider_name",
            "duration_minutes",
            "max_services_per_slot",
            "max_notice_days",
            "available_time_slots",
        ]
        # If you want a custom list serializer that supports "updates", 
        # you can set:
        # list_serializer_class = SomeUpdateListSerializer
        
    def get_available_time_slots(self, obj):
        """
        Call the Service model method that calculates available slots.
        """
        return obj.get_available_time_slots()
    
class AttributeOptionSerializer(serializers.ModelSerializer):
    """
    Serializer for AttributeOption to include the price field.
    """
    class Meta:
        model = AttributeOption
        fields = ['id', 'option', 'price']  # Include 'price' here


class AttributeOptionGroupSerializer(OscarHyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="attributeoptiongroup-detail"
    )
    options = AttributeOptionSerializer(many=True, required=True)

    def create(self, validated_data):
        options_data = validated_data.pop('options', [])
        instance = super().create(validated_data)
        options = [AttributeOption.objects.get_or_create(**option_data)[0] for option_data in options_data]
        instance.options.set(options)
        return instance

    def update(self, instance, validated_data):
        options_data = validated_data.pop('options', [])
        instance = super().update(instance, validated_data)
        options = [AttributeOption.objects.get_or_create(**option_data)[0] for option_data in options_data]
        instance.options.set(options)
        return instance

    class Meta:
        model = AttributeOptionGroup
        fields = ("id", "url", "name", "code", "options")
        
        depth = 1



class BaseCategorySerializer(OscarHyperlinkedModelSerializer):
    breadcrumbs = serializers.CharField(source="full_name", read_only=True)
    vendor = serializers.HyperlinkedRelatedField(
        view_name="vendor-detail",
        queryset=Vendor.objects.all(),
        lookup_field="pk",
    )
    id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        exclude = ("path", "depth", "numchild")


class CategorySerializer(BaseCategorySerializer):
    children = serializers.HyperlinkedIdentityField(
        view_name="category-child-list",
        # lookup_field="full_slug",
        lookup_url_kwarg="breadcrumbs",
    )

    products = serializers.SerializerMethodField()

    def get_products(self, obj):
        products = getattr(obj, "filtered_products", [])
        return ProductSerializer(products, many=True, context=self.context).data


    # class Meta(BaseCategorySerializer.Meta):
    #     fields = BaseCategorySerializer.Meta.fields + ['children', 'products']



class ProductAttributeListSerializer(UpdateListSerializer):
    def select_existing_item(self, manager, datum):
        try:
            return manager.get(product_class=datum["product_class"], code=datum["code"])
        except manager.model.DoesNotExist:
            pass
        except manager.model.MultipleObjectsReturned as e:
            logger.error("Multiple objects on unique contrained items, freaky %s", e)
            logger.exception(e)

        return None


class ProductAttributeSerializer(OscarHyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="admin-productattribute-detail"
    )
    product_class = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=ProductClass.objects.get_queryset(),
        write_only=True,
        required=False,
    )
    option_group = AttributeOptionGroupSerializer(required=False, allow_null=True)

    def create(self, validated_data):
        option_group = validated_data.pop("option_group", None)
        instance = super(ProductAttributeSerializer, self).create(validated_data)
        return self.update(instance, {"option_group": option_group})

    def update(self, instance, validated_data):
        option_group = validated_data.pop("option_group", None)
        updated_instance = super(ProductAttributeSerializer, self).update(
            instance, validated_data
        )
        if option_group is not None:
            serializer = self.fields["option_group"]
            # use the serializer to update the attribute_values
            if instance.option_group:
                updated_instance.option_group = serializer.update(
                    instance.option_group, option_group
                )
            else:
                updated_instance.option_group = serializer.create(option_group)

            updated_instance.save()

        return updated_instance

    class Meta:
        model = ProductAttribute
        list_serializer_class = ProductAttributeListSerializer
        fields = "__all__"


class RangeSerializer(OscarHyperlinkedModelSerializer):
    class Meta:
        model = Range
        fields = "__all__"


class PartnerSerializer(OscarHyperlinkedModelSerializer):
    class Meta:
        model = Partner
        fields = "__all__"


class OptionSerializer(OscarHyperlinkedModelSerializer):
    code = serializers.SlugField()
    option_group = AttributeOptionGroupSerializer(required=True)
    class Meta:
        model = Option
        fields = settings.OPTION_FIELDS
        list_serializer_class = UpdateForwardManyToManySerializer
        depth = 1

class ProductAttributeValueListSerializer(UpdateListSerializer):
    # pylint: disable=unused-argument
    def shortcut_to_internal_value(self, data, productclass, attributes):
        difficult_attributes = {
            at.code: at
            for at in productclass.attributes.filter(
                type__in=[
                    ProductAttribute.OPTION,
                    ProductAttribute.MULTI_OPTION,
                    ProductAttribute.DATE,
                    ProductAttribute.DATETIME,
                    ProductAttribute.ENTITY,
                    ProductAttribute.FILE,
                    ProductAttribute.IMAGE,
                ]
            )
        }
        cv = AttributeConverter(self.context)
        internal_value = []
        for item in data:
            code, value = getitems(item, "code", "value")
            if code is None:  # delegate error state to child serializer
                internal_value.append(self.child.to_internal_value(item))

            if code in difficult_attributes:
                attribute = difficult_attributes[code]
                converted_value = cv.to_attribute_type_value(attribute, code, value)
                internal_value.append(
                    {
                        "value": converted_value,
                        "attribute": attribute,
                        "product_class": productclass,
                    }
                )
            else:
                internal_value.append(
                    {
                        "value": value,
                        "attribute": code,
                        "product_class": productclass,
                    }
                )

        return internal_value

    def to_internal_value(self, data):
        productclasses = set()
        attributes = set()
        parent = None

        for item in data:
            product_class, code = getitems(item, "product_class", "code")
            if product_class:
                productclasses.add(product_class)
            if "parent" in item and item["parent"] is not None:
                parent = item["parent"]
            attributes.add(code)

        # if all attributes belong to the same productclass, everything is just
        # as expected and we can take a shortcut by only resolving the
        # productclass to the model instance and nothing else.
        attrs_valid = all(attributes)  # no missing attribute codes?
        if attrs_valid:
            try:
                if len(productclasses):
                    (product_class,) = productclasses
                    pc = ProductClass.objects.get(slug=product_class)
                    return self.shortcut_to_internal_value(data, pc, attributes)
                elif parent:
                    pc = ProductClass.objects.get(products__id=parent)
                    return self.shortcut_to_internal_value(data, pc, attributes)
            except ProductClass.DoesNotExist:
                pass

        # if we get here we can't take the shortcut, just let everything be
        # processed by the original serializer and handle the errors.
        return super().to_internal_value(data)

    def get_value(self, dictionary):
        values = super(ProductAttributeValueListSerializer, self).get_value(dictionary)
        if values is empty:
            return values

        product_class, parent = getitems(dictionary, "product_class", "parent")
        return [
            dict(value, product_class=product_class, parent=parent) for value in values
        ]

    def to_representation(self, data):
        if isinstance(data, Manager):
            # use a cached query from product.attr to get the attributes instead
            # if an silly .all() that clones the queryset and performs a new query
            _, product = self.get_name_and_rel_instance(data)
            iterable = product.attr.get_values()
        else:
            iterable = data

        return [self.child.to_representation(item) for item in iterable]

    def update(self, instance, validated_data):
        assert isinstance(instance, Manager)

        _, product = self.get_name_and_rel_instance(instance)

        attr_codes = []
        product.attr.initialize()
        for validated_datum in validated_data:
            # leave all the attribute saving to the ProductAttributesContainer instead
            # of the child serializers
            attribute, value = getitems(validated_datum, "attribute", "value")
            if hasattr(
                attribute, "code"
            ):  # if the attribute is a model instance use the code
                product.attr.set(attribute.code, value, validate_identifier=False)
                attr_codes.append(attribute.code)
            else:
                product.attr.set(attribute, value, validate_identifier=False)
                attr_codes.append(attribute)

        # if we don't clear the dirty attributes all parent attributes
        # are marked as explicitly set, so they will be copied to the
        # child product.
        product.attr._dirty.clear()  # pylint: disable=protected-access
        product.attr.save()
        # we have to make sure to use the correct db_manager in a multidatabase
        # context, we make sure to use the same database as the passed in manager
        local_attribute_values = product.attribute_values.db_manager(
            instance.db
        ).filter(attribute__code__in=attr_codes)
        return list(local_attribute_values)


class ProductAttributeValueSerializer(OscarModelSerializer):
    # we declare the product as write_only since this serializer is meant to be
    # used nested inside a product serializer.
    product = serializers.PrimaryKeyRelatedField(
        many=False, write_only=True, required=False, queryset=Product.objects
    )

    value = AttributeValueField()  # handles different attribute value types
    # while code is specified as read_only, it is still required, because otherwise
    # the attribute is unknown, so while it will never be overwritten, you do
    # have to include it in your data structure
    code = serializers.CharField(source="attribute.code", read_only=True)
    name = serializers.CharField(
        source="attribute.name", required=False, read_only=True
    )

    def to_internal_value(self, data):
        try:
            internal_value = super(
                ProductAttributeValueSerializer, self
            ).to_internal_value(data)
            internal_value["product_class"] = data.get("product_class")
            return internal_value
        except FieldError as e:
            raise serializers.ValidationError(e.detail)

    def save(self, **kwargs):
        """
        Since there is a unique constraint, sometimes we want to update instead
        of creating a new object (because an integrity error would occur due
        to the constraint on attribute and product). If instance is set, the
        update method will be used instead of the create method.
        """
        data = deepcopy(kwargs)
        data.update(self.validated_data)
        return self.update_or_create(data)

    def update_or_create(self, validated_data):
        value = validated_data["value"]
        product = validated_data["product"]
        attribute = validated_data["attribute"]
        attribute.save_value(product, value)
        return product.attribute_values.get(attribute=attribute)

    create = update_or_create

    def update(self, instance, validated_data):
        data = deepcopy(validated_data)
        data["product"] = instance.product
        return self.update_or_create(data)

    class Meta:
        model = ProductAttributeValue
        list_serializer_class = ProductAttributeValueListSerializer
        fields = settings.PRODUCT_ATTRIBUTE_VALUE_FIELDS


class ProductImageUpdateListSerializer(UpdateListSerializer):
    "Select existing image based on hash of image content"

    def select_existing_item(self, manager, datum):
        # determine the hash of the passed image
        target_file_hash = file_hash(datum["original"])
        for image in manager.all():  # search for a match in the set of exising images
            _hash = file_hash(image.original)
            if _hash == target_file_hash:
                # django will create a copy of the original under a weird name,
                # because the image is freshly fetched, except if we use the
                # original image FileObject
                datum["original"] = image.original
                return image

        return None


class ProductImageSerializer(OscarModelSerializer):
    product = serializers.PrimaryKeyRelatedField(
        write_only=True, required=False, queryset=Product.objects
    )
    original = serializers.ImageField(required=False)
    
    def create(self, validated_data):
        """
        Handle image upload when creating a new product image.
        """
        return super().create(validated_data)
    
    def update(self, instance, validated_data):
        """
        Handle image upload when updating an existing product image.
        """
        return super().update(instance, validated_data)

    class Meta:
        model = ProductImage
        fields = "__all__"
        list_serializer_class = ProductImageUpdateListSerializer


class AvailabilitySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    is_available_to_buy = serializers.BooleanField()
    num_available = serializers.IntegerField(required=False)
    message = serializers.CharField()


class RecommmendedProductSerializer(OscarModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="product-detail")

    class Meta:
        model = Product
        fields = settings.RECOMMENDED_PRODUCT_FIELDS


class ProductStockRecordSerializer(OscarModelSerializer):
    available_to_buy = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    
    def get_available_to_buy(self, obj):
        """
        Calculate the available to buy quantity as num_in_stock - num_allocated.
        """
        try:
            # Handle the case where num_in_stock or num_allocated might be None
            num_in_stock = obj.num_in_stock or 0
            num_allocated = obj.num_allocated or 0
            return max(0, num_in_stock - num_allocated)
        except Exception:
            # Return 0 as a safe default if any error occurs
            return 0
    def get_in_stock(self, obj):
        """
        Check if the product is in stock.
        """
        try:
            # Handle the case where num_in_stock or num_allocated might be None
            num_in_stock = obj.num_in_stock or 0
            num_allocated = obj.num_allocated or 0
            return max(0, num_in_stock - num_allocated) > 0
        except Exception:
            # Return False as a safe default if any error occurs
            return False
    
    class Meta:
        model = StockRecord
        fields = "__all__"


class BaseProductSerializer(OscarModelSerializer):
    "Base class shared by admin and public serializer"
    attributes = ProductAttributeValueSerializer(
        many=True, required=False, source="attribute_values"
    )
    # categories = CategorySerializer(many=True, required=False)
    product_class = serializers.SlugRelatedField(
        slug_field="slug", queryset=ProductClass.objects, allow_null=True
    )
    options = OptionSerializer(many=True, required=False)
    recommended_products = serializers.HyperlinkedRelatedField(
        view_name="product-detail",
        many=True,
        required=False,
        queryset=Product.objects.filter(
            structure__in=[Product.PARENT, Product.STANDALONE]
        ),
    )

    def validate(self, attrs):
        if "structure" in attrs and "parent" in attrs:
            if attrs["structure"] == Product.CHILD and attrs["parent"] is None:
                raise serializers.ValidationError(_("child without parent"))
        if "structure" in attrs and "product_class" in attrs:
            if attrs["product_class"] is None and attrs["structure"] != Product.CHILD:
                raise serializers.ValidationError(
                    _("product_class can not be empty for structure %(structure)s")
                    % attrs
                )

        return super(BaseProductSerializer, self).validate(attrs)

    class Meta:
        model = Product


class PublicProductSerializer(BaseProductSerializer):
    "Serializer base class used for public products api"
    url = serializers.HyperlinkedIdentityField(view_name="product-detail")
    # price = serializers.HyperlinkedIdentityField(
    #     view_name="product-price", read_only=True
    # )
    availability = serializers.HyperlinkedIdentityField(
        view_name="product-availability", read_only=True
    )

    def get_field_names(self, declared_fields, info):
        """
        Override get_field_names to make sure that we are not getting errors
        for not including declared fields.
        """
        return super(PublicProductSerializer, self).get_field_names({}, info)


class ChildProductSerializer(PublicProductSerializer):
    "Serializer for child products"
    parent = serializers.HyperlinkedRelatedField(
        view_name="product-detail",
        queryset=Product.objects.filter(structure=Product.PARENT),
    )
    # the below fields can be filled from the parent product if enabled.
    images = ProductImageSerializer(many=True, required=False, source="parent.images")
    description = serializers.CharField(source="parent.description")
    stockrecords = ProductStockRecordSerializer(many=True, required=False)

    class Meta(PublicProductSerializer.Meta):
        fields = settings.CHILDPRODUCTDETAIL_FIELDS

class VendorSerializer(serializers.ModelSerializer):

    class Meta:
        model = Vendor
        fields = ['id', 'name']
        
class ProductSerializer(PublicProductSerializer):
    "Serializer for public api with strategy fields added for price and availability"
    # url = serializers.HyperlinkedIdentityField(view_name="product-detail")
    # price = serializers.HyperlinkedIdentityField(
    #     view_name="product-price", read_only=True
    # )
    vendor = VendorSerializer(read_only=True)
    availability = serializers.HyperlinkedIdentityField(
        view_name="product-availability", read_only=True
    )

    images = ProductImageSerializer(many=True, required=False)
    children = ChildProductSerializer(many=True, required=False)
    # stockrecords = serializers.HyperlinkedIdentityField(
    #     view_name="product-stockrecords", read_only=True
    # )

    services = ServiceSerializer(
        many=True,
        required=False,
        source="service",  # or 'services' if using a ManyToMany
    )
    stockrecords = serializers.SerializerMethodField()
    allergens = serializers.SerializerMethodField()
    
    def get_allergens(self, obj):
        # Lazy import to avoid circular dependency
        from server.apps.catalogue.serializers import AllergenSerializer
        return AllergenSerializer(obj.allergens.all(), many=True).data
        
    def get_stockrecords(self, obj):
            """
            Retrieve the stock record for the product based on the branch_id.
            """
            # Step 1: Try to get branch_id from the query (assuming it's passed in the request)
            branch_id = self.context["request"].query_params.get("branch_id")
            
            # Also check for "branch" parameter since CategoryList uses "branch" not "branch_id"
            if not branch_id:
                branch_id = self.context["request"].query_params.get("branch")

            # Step 2: If not from the query, try to get it from the basket
            if not branch_id:
                basket = operations.get_basket(self.context["request"])
                branch_id = basket.branch

            # Step 3: If still not available, fall back to the user's vendor staff branch ID
            if not branch_id and hasattr(self.context["request"].user, 'user_vendor_staff'):
                branch_id = self.context["request"].user.user_vendor_staff.branch.id

            # Step 4: Attempt to fetch the stock record for the determined branch_id
            try:
                stockrecord = obj.stockrecords.get(branch_id=branch_id)
                return ProductStockRecordSerializer(stockrecord).data
            except StockRecord.DoesNotExist:
                return {}
            
    class Meta(PublicProductSerializer.Meta):
        fields = settings.PRODUCTDETAIL_FIELDS


class ProductLinkSerializer(ProductSerializer):
    """
    Summary serializer for list view, listing all products.

    This serializer can be easily made to show any field on ``ProductSerializer``,
    just add fields to the ``OSCARAPI_PRODUCT_FIELDS`` setting.
    """

    class Meta(PublicProductSerializer.Meta):
        fields = settings.PRODUCT_FIELDS


class OptionValueSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    option = serializers.HyperlinkedRelatedField(
        view_name="option-detail", queryset=Option.objects
    )
    value = serializers.CharField()


class AddProductSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializes and validates an add to basket request.
    """

    quantity = serializers.IntegerField(required=True)
    branch_id = serializers.IntegerField(required=True)
    confirm = serializers.BooleanField(required=False)
    url = serializers.HyperlinkedRelatedField(
        view_name="product-detail", queryset=Product.objects, required=True
    )
    options = OptionValueSerializer(many=True, required=False)

    