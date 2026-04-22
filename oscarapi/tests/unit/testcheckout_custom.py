from decimal import Decimal
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import warnings

from django.db import IntegrityError
from django.test import SimpleTestCase
from django.utils import timezone
from django.urls import NoReverseMatch
from rest_framework import exceptions, serializers

from oscar.core import prices
from oscarapi.serializers.checkout import (
    CheckoutSerializer,
    DetailedOrderSerializer,
    OrderNotificationSerializer,
    OrderSerializer,
    UserAddressSerializer,
)
from oscarapi.views.checkout import CheckoutView, LastOrderRatingPopupView, OrderList


class CheckoutSerializerValidationTests(SimpleTestCase):
    def make_serializer(self, user=None):
        request = SimpleNamespace(
            user=user or SimpleNamespace(id=5, is_anonymous=False),
        )
        serializer = CheckoutSerializer(context={"request": request})
        return serializer, request

    def make_basket(self, *, num_items=1, lines=None):
        basket = SimpleNamespace(
            num_items=num_items,
            all_lines=lambda: lines or [],
        )
        return basket

    def make_line(self, *, stock=10, allocated=0, quantity=1, title="Product"):
        product = SimpleNamespace(get_title=lambda: title)
        stockrecord = SimpleNamespace(num_in_stock=stock, num_allocated=allocated)
        return SimpleNamespace(
            stockrecord=stockrecord,
            quantity=quantity,
            product=product,
        )

    def test_validate_rejects_empty_basket(self):
        serializer, request = self.make_serializer()
        basket = self.make_basket(num_items=0)

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ):
            with self.assertRaises(serializers.ValidationError) as exc:
                serializer.validate({"basket": basket})

        self.assertIn("Cannot checkout with empty basket", str(exc.exception))

    def test_validate_rejects_out_of_stock_lines(self):
        serializer, request = self.make_serializer()
        line = self.make_line(stock=0, allocated=0, quantity=1, title="Burger")
        basket = self.make_basket(lines=[line])

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ):
            with self.assertRaises(serializers.ValidationError) as exc:
                serializer.validate({"basket": basket})

        self.assertEqual(
            exc.exception.detail,
            {"availability": ["'Burger' is out of stock. Please adjust your basket to continue"]},
        )

    def test_validate_rejects_quantity_above_available_stock(self):
        serializer, request = self.make_serializer()
        line = self.make_line(stock=3, allocated=1, quantity=3, title="Pizza")
        basket = self.make_basket(lines=[line])

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ):
            with self.assertRaises(serializers.ValidationError) as exc:
                serializer.validate({"basket": basket})

        self.assertEqual(
            exc.exception.detail,
            {"availability": ["'Pizza' only has 2 available, but you requested 3."]},
        )

    def test_validate_rejects_shipping_charge_tampering(self):
        serializer, request = self.make_serializer()
        basket = self.make_basket()
        shipping_method = SimpleNamespace(
            calculate=lambda basket: prices.Price("SAR", Decimal("3.00"), Decimal("3.00"))
        )

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ), patch.object(
            serializer, "_shipping_method", return_value=shipping_method
        ), patch(
            "oscarapi.serializers.checkout.OrderTotalCalculator"
        ) as total_calculator:
            total_calculator.return_value.calculate.return_value = prices.Price(
                "SAR", Decimal("13.00"), Decimal("13.00")
            )
            with self.assertRaises(serializers.ValidationError) as exc:
                serializer.validate(
                    {
                        "basket": basket,
                        "shipping_charge": {
                            "currency": "SAR",
                            "excl_tax": Decimal("5.00"),
                            "incl_tax": Decimal("5.00"),
                        },
                    }
                )

        self.assertIn("Shipping price incorrect", str(exc.exception))

    def test_validate_rejects_total_tampering(self):
        serializer, request = self.make_serializer()
        basket = self.make_basket()
        shipping_method = SimpleNamespace(
            calculate=lambda basket: prices.Price("SAR", Decimal("3.00"), Decimal("3.00"))
        )

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ), patch.object(
            serializer, "_shipping_method", return_value=shipping_method
        ), patch(
            "oscarapi.serializers.checkout.OrderTotalCalculator"
        ) as total_calculator:
            total_calculator.return_value.calculate.return_value = prices.Price(
                "SAR", Decimal("13.00"), Decimal("13.00")
            )
            with self.assertRaises(serializers.ValidationError) as exc:
                serializer.validate({"basket": basket, "total": Decimal("99.00")})

        self.assertIn("Total incorrect", str(exc.exception))

    def test_validate_rejects_foreign_vehicle(self):
        serializer, request = self.make_serializer(user=SimpleNamespace(id=5, is_anonymous=False))
        basket = self.make_basket()
        shipping_method = SimpleNamespace(
            calculate=lambda basket: prices.Price("SAR", Decimal("3.00"), Decimal("3.00"))
        )
        vehicle = SimpleNamespace(customer=SimpleNamespace(user=SimpleNamespace(id=9)))

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ), patch.object(
            serializer, "_shipping_method", return_value=shipping_method
        ), patch(
            "oscarapi.serializers.checkout.OrderTotalCalculator"
        ) as total_calculator:
            total_calculator.return_value.calculate.return_value = prices.Price(
                "SAR", Decimal("13.00"), Decimal("13.00")
            )
            with self.assertRaises(serializers.ValidationError) as exc:
                serializer.validate({"basket": basket, "vehicle": vehicle})

        self.assertIn("does not belong to you", str(exc.exception))

    def test_validate_enriches_attrs_with_checkout_data(self):
        serializer, request = self.make_serializer(user=SimpleNamespace(id=5, is_anonymous=False))
        basket = self.make_basket()
        shipping_method = SimpleNamespace(
            code="express",
            calculate=lambda basket: prices.Price("SAR", Decimal("3.00"), Decimal("3.00")),
        )
        vehicle = SimpleNamespace(customer=SimpleNamespace(user=SimpleNamespace(id=5)))

        with patch(
            "oscarapi.serializers.checkout.assign_basket_strategy", return_value=basket
        ), patch.object(
            serializer, "_shipping_method", return_value=shipping_method
        ), patch(
            "oscarapi.serializers.checkout.OrderTotalCalculator"
        ) as total_calculator:
            total = prices.Price("SAR", Decimal("13.00"), Decimal("13.00"))
            total_calculator.return_value.calculate.return_value = total
            attrs = serializer.validate({"basket": basket, "vehicle": vehicle})

        self.assertIs(attrs["basket"], basket)
        self.assertIs(attrs["shipping_method"], shipping_method)
        self.assertEqual(attrs["shipping_charge"], prices.Price("SAR", Decimal("3.00"), Decimal("3.00")))
        self.assertIs(attrs["order_total"], total)


class CheckoutSerializerCreateTests(SimpleTestCase):
    def test_create_builds_addresses_and_calls_place_order(self):
        request = SimpleNamespace(user=SimpleNamespace(id=4))
        serializer = CheckoutSerializer(context={"request": request})
        basket = SimpleNamespace(id=2)
        shipping_method = object()
        shipping_charge = object()
        order_total = object()
        vehicle = object()

        validated_data = {
            "basket": basket,
            "shipping_method": shipping_method,
            "shipping_charge": shipping_charge,
            "order_total": order_total,
            "guest_email": "guest@example.com",
            "vehicle": vehicle,
            "shipping_address": {"line1": "Street"},
            "billing_address": {"line1": "Billing"},
        }

        with patch.object(serializer, "generate_order_number", return_value="ORD-1"), patch.object(
            serializer, "place_order", return_value="placed-order"
        ) as place_order:
            result = serializer.create(dict(validated_data))

        self.assertEqual(result, "placed-order")
        self.assertEqual(place_order.call_args.kwargs["order_number"], "ORD-1")
        self.assertEqual(place_order.call_args.kwargs["guest_email"], "guest@example.com")
        self.assertIs(place_order.call_args.kwargs["vehicle"], vehicle)
        self.assertEqual(place_order.call_args.kwargs["shipping_address"].line1, "Street")
        self.assertEqual(place_order.call_args.kwargs["billing_address"].line1, "Billing")

    def test_create_wraps_value_error_as_not_acceptable(self):
        request = SimpleNamespace(user=SimpleNamespace(id=4))
        serializer = CheckoutSerializer(context={"request": request})

        with patch.object(serializer, "generate_order_number", return_value="ORD-1"), patch.object(
            serializer, "place_order", side_effect=ValueError("invalid")
        ):
            with self.assertRaises(exceptions.NotAcceptable) as exc:
                serializer.create({"basket": SimpleNamespace(id=1)})

        self.assertEqual(str(exc.exception.detail), "invalid")

    def test_shipping_method_prefers_requested_code(self):
        request = SimpleNamespace(user=SimpleNamespace(id=1))
        serializer = CheckoutSerializer(context={"request": request})
        basket = object()
        default = SimpleNamespace(code="default")
        express = SimpleNamespace(code="express")
        repository = MagicMock()
        repository.get_default_shipping_method.return_value = default
        repository.get_shipping_methods.return_value = [default, express]

        with patch("oscarapi.serializers.checkout.Repository", return_value=repository):
            result = serializer._shipping_method(request, basket, "express", None)

        self.assertIs(result, express)

    def test_shipping_method_falls_back_to_default(self):
        request = SimpleNamespace(user=SimpleNamespace(id=1))
        serializer = CheckoutSerializer(context={"request": request})
        basket = object()
        default = SimpleNamespace(code="default")
        repository = MagicMock()
        repository.get_default_shipping_method.return_value = default

        with patch("oscarapi.serializers.checkout.Repository", return_value=repository):
            result = serializer._shipping_method(request, basket, None, None)

        self.assertIs(result, default)


class CheckoutRelatedSerializerTests(SimpleTestCase):
    def test_user_address_serializer_create_assigns_request_user(self):
        request = SimpleNamespace(user=SimpleNamespace(id=6))
        serializer = UserAddressSerializer(context={"request": request})

        with patch(
            "oscarapi.serializers.checkout.OscarModelSerializer.create",
            return_value="created-address",
        ) as parent_create:
            result = serializer.create({"line1": "Street"})

        self.assertEqual(result, "created-address")
        self.assertEqual(parent_create.call_args.args[0]["user"], request.user)

    def test_user_address_serializer_create_wraps_integrity_error(self):
        request = SimpleNamespace(user=SimpleNamespace(id=6))
        serializer = UserAddressSerializer(context={"request": request})

        with patch(
            "oscarapi.serializers.checkout.OscarModelSerializer.create",
            side_effect=IntegrityError("duplicate"),
        ):
            with self.assertRaises(exceptions.NotAcceptable) as exc:
                serializer.create({"line1": "Street"})

        self.assertIn("duplicate", str(exc.exception.detail))

    def test_user_address_serializer_update_assigns_request_user(self):
        request = SimpleNamespace(user=SimpleNamespace(id=6))
        serializer = UserAddressSerializer(context={"request": request})
        instance = object()

        with patch(
            "oscarapi.serializers.checkout.OscarModelSerializer.update",
            return_value="updated-address",
        ) as parent_update:
            result = serializer.update(instance, {"line1": "Street"})

        self.assertEqual(result, "updated-address")
        self.assertIs(parent_update.call_args.args[0], instance)
        self.assertEqual(parent_update.call_args.args[1]["user"], request.user)

    def test_order_serializer_get_branch_returns_store_summary(self):
        serializer = OrderSerializer()
        store = SimpleNamespace(id=3)

        with patch("oscarapi.serializers.checkout.StoreListSerializer") as store_serializer:
            store_serializer.return_value.data = {"id": 3, "name": "Downtown"}
            branch = serializer.get_branch(SimpleNamespace(store=store))

        self.assertEqual(branch, {"id": 3, "name": "Downtown"})

    def test_order_serializer_get_payment_url_warns_when_route_is_missing(self):
        serializer = OrderSerializer()
        order = SimpleNamespace(pk=5)

        with patch(
            "oscarapi.serializers.checkout.reverse", side_effect=NoReverseMatch()
        ), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            message = serializer.get_payment_url(order)

        self.assertIn("api-payment", message)
        self.assertTrue(caught)

    def test_order_notification_serializer_serializes_basket_summary(self):
        serializer = OrderNotificationSerializer()
        basket = SimpleNamespace(
            id=3,
            total_excl_tax=Decimal("10.00"),
            total_excl_tax_excl_discounts=Decimal("12.00"),
            total_incl_tax=Decimal("10.00"),
            total_incl_tax_excl_discounts=Decimal("12.00"),
            total_tax=Decimal("0.00"),
            currency="SAR",
            branch=SimpleNamespace(
                id=4,
                name="Downtown",
                location=SimpleNamespace(x=35.9, y=31.9),
                vendor=SimpleNamespace(id=7, name="Vendor"),
            ),
            lines=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        id=11,
                        quantity=2,
                        product=SimpleNamespace(
                            id=9,
                            title="Burger",
                            original_price=Decimal("5.00"),
                            selling_price=Decimal("4.50"),
                            price_currency="SAR",
                            get_all_images=lambda: [
                                SimpleNamespace(original=SimpleNamespace(url="https://img/p.png"))
                            ],
                        ),
                        attributes=SimpleNamespace(
                            all=lambda: [SimpleNamespace(value="Cheese", option=SimpleNamespace(name="Topping"))]
                        ),
                    )
                ]
            ),
        )

        data = serializer._serialize_basket(basket)

        self.assertEqual(data["vendor"], {"id": 7, "name": "Vendor"})
        self.assertEqual(data["branch"]["id"], 4)
        self.assertEqual(data["products_in_basket"]["9"][0]["title"], "Burger")

    def test_detailed_order_serializer_get_products_in_basket(self):
        serializer = DetailedOrderSerializer()
        basket = SimpleNamespace(
            currency="SAR",
            lines=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        id=1,
                        quantity=2,
                        line_price_excl_tax=Decimal("9.00"),
                        line_price_incl_tax=Decimal("9.00"),
                        product=SimpleNamespace(
                            id=3,
                            get_title=lambda: "Pizza",
                            get_all_images=lambda: [
                                SimpleNamespace(original=SimpleNamespace(url="https://img/pizza.png"))
                            ],
                        ),
                        attributes=SimpleNamespace(
                            all=lambda: [SimpleNamespace(value="Large", option=SimpleNamespace(name="Size"))]
                        ),
                    )
                ]
            ),
        )

        products = serializer._get_products_in_basket(basket)

        self.assertEqual(
            products,
            [
                {
                    "id": 1,
                    "product_id": 3,
                    "title": "Pizza",
                    "quantity": 2,
                    "attributes": [{"value": "Large", "name": "Size"}],
                    "price_excl_tax": 9.0,
                    "price_incl_tax": 9.0,
                    "price_currency": "SAR",
                    "images": "https://img/pizza.png",
                }
            ],
        )

    def test_detailed_order_serializer_get_order_rating(self):
        serializer = DetailedOrderSerializer()
        rating_model = SimpleNamespace(objects=MagicMock())
        rating_model.objects.filter.return_value.first.return_value = SimpleNamespace(rating=4)
        order = object()

        with patch("oscar.core.loading.get_model", return_value=rating_model):
            rating = serializer.get_order_rating(order)

        self.assertEqual(rating, 4)


class CheckoutViewTests(SimpleTestCase):
    def test_post_rejects_unauthorized_basket_access(self):
        view = CheckoutView()
        request = SimpleNamespace(data={"basket": "/api/baskets/1/"}, user=SimpleNamespace())
        basket = object()

        with patch("oscarapi.views.checkout.parse_basket_from_hyperlink", return_value=basket), patch(
            "oscarapi.views.checkout.request_allows_access_to_basket", return_value=False
        ):
            response = view.post(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, "Unauthorized")

    def test_post_saves_order_freezes_basket_and_sends_signal(self):
        view = CheckoutView()
        request = SimpleNamespace(data={"basket": "/api/baskets/1/"}, user=SimpleNamespace())
        basket = MagicMock()
        order = object()
        checkout_serializer = MagicMock()
        checkout_serializer.is_valid.return_value = True
        checkout_serializer.save.return_value = order
        order_serializer = MagicMock()
        order_serializer.data = {"number": "ORD-1"}
        view.serializer_class = MagicMock(return_value=checkout_serializer)
        view.order_serializer_class = MagicMock(return_value=order_serializer)

        with patch("oscarapi.views.checkout.parse_basket_from_hyperlink", return_value=basket), patch(
            "oscarapi.views.checkout.request_allows_access_to_basket", return_value=True
        ), patch("oscarapi.views.checkout.oscarapi_post_checkout.send") as post_checkout:
            response = view.post(request)

        self.assertEqual(response.status_code, 200)
        basket.freeze.assert_called_once_with()
        post_checkout.assert_called_once()
        self.assertEqual(response.data, {"number": "ORD-1"})


class OrderListTests(SimpleTestCase):
    def test_get_queryset_filters_by_user_and_statuses(self):
        view = OrderList()
        user = SimpleNamespace(id=5)
        query_params = SimpleNamespace(getlist=lambda key: ["Pending", "Finished"] if key == "status" else [])
        view.request = SimpleNamespace(user=user, query_params=query_params)
        queryset = MagicMock()
        filtered_queryset = MagicMock()
        queryset.filter.return_value = filtered_queryset

        with patch("oscarapi.views.checkout.Order.objects.filter", return_value=queryset) as filter_orders:
            result = view.get_queryset()

        filter_orders.assert_called_once_with(user=user)
        queryset.filter.assert_called_once_with(status__in=["Pending", "Finished"])
        self.assertIs(result, filtered_queryset)

    def test_get_queryset_without_statuses_returns_user_orders(self):
        view = OrderList()
        user = SimpleNamespace(id=5)
        query_params = SimpleNamespace(getlist=lambda key: [])
        view.request = SimpleNamespace(user=user, query_params=query_params)
        queryset = MagicMock()

        with patch("oscarapi.views.checkout.Order.objects.filter", return_value=queryset):
            result = view.get_queryset()

        self.assertIs(result, queryset)


class LastOrderRatingPopupViewTests(SimpleTestCase):
    def test_get_object_returns_none_when_no_finished_order_exists(self):
        view = LastOrderRatingPopupView()
        view.request = SimpleNamespace(user=SimpleNamespace(id=5))
        queryset = MagicMock()
        queryset.order_by.return_value.first.return_value = None

        with patch("oscarapi.views.checkout.Order.objects.filter", return_value=queryset):
            result = view.get_object()

        self.assertIsNone(result)

    def test_get_object_turns_off_popup_for_old_finished_order(self):
        view = LastOrderRatingPopupView()
        user = SimpleNamespace(id=5)
        view.request = SimpleNamespace(user=user)
        order = SimpleNamespace(
            pk=7,
            show_rating_popup=True,
            date_placed=timezone.now() - timedelta(hours=2),
            save=MagicMock(),
        )
        response_order = SimpleNamespace(pk=7)
        queryset = MagicMock()
        queryset.order_by.return_value.first.return_value = order

        with patch("oscarapi.views.checkout.Order.objects.filter", return_value=queryset), patch(
            "oscarapi.views.checkout.Order.objects.get", return_value=response_order
        ), patch("oscarapi.views.checkout.transaction.atomic", return_value=nullcontext()):
            result = view.get_object()

        self.assertIs(result, response_order)
        self.assertFalse(order.show_rating_popup)
        order.save.assert_called_once_with(update_fields=["show_rating_popup"])

    def test_get_object_returns_none_when_popup_window_not_reached(self):
        view = LastOrderRatingPopupView()
        user = SimpleNamespace(id=5)
        view.request = SimpleNamespace(user=user)
        order = SimpleNamespace(
            pk=7,
            show_rating_popup=True,
            date_placed=timezone.now(),
            save=MagicMock(),
        )
        queryset = MagicMock()
        queryset.order_by.return_value.first.return_value = order

        with patch("oscarapi.views.checkout.Order.objects.filter", return_value=queryset):
            result = view.get_object()

        self.assertIsNone(result)
        order.save.assert_not_called()
