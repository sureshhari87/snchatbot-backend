# Android Retrofit Integration Guide

Use this guide to wire your Android app to the deployed backend:

```text
https://sureshhari-snchatbot-backend.hf.space
```

The app should call `GET /mobile/config` on launch, then wire screens using the endpoints below. Native Android requests are not blocked by CORS; CORS only matters for browser-based admin panels.

## Recommended Android Dependencies

Add the current stable versions your Android project already uses for these libraries:

```kotlin
implementation("com.squareup.retrofit2:retrofit:<version>")
implementation("com.squareup.retrofit2:converter-moshi:<version>")
implementation("com.squareup.okhttp3:okhttp:<version>")
implementation("com.squareup.okhttp3:logging-interceptor:<version>")
implementation("com.squareup.moshi:moshi-kotlin:<version>")
implementation("androidx.security:security-crypto:<version>")
implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:<version>")
```

Use AndroidX Security encrypted storage for tokens. Do not store access or refresh tokens in plain `SharedPreferences`.

## Core Retrofit Service

```kotlin
interface JewelleryApi {
    @GET("mobile/config")
    suspend fun mobileConfig(): MobileConfig

    @POST("register")
    suspend fun register(@Body body: RegisterRequest): UserOut

    @FormUrlEncoded
    @POST("login")
    suspend fun login(
        @Field("username") email: String,
        @Field("password") password: String
    ): TokenResponse

    @POST("refresh")
    suspend fun refresh(@Body body: RefreshTokenRequest): TokenResponse

    @POST("logout")
    suspend fun logout(@Body body: RefreshTokenRequest): MessageResponse

    @POST("logout-all-devices")
    suspend fun logoutAllDevices(): MessageResponse

    @GET("me")
    suspend fun me(): UserOut

    @POST("forgot-password")
    suspend fun forgotPassword(@Body body: ForgotPasswordRequest): MessageResponse

    @POST("resend-verification")
    suspend fun resendVerification(@Body body: ResendVerificationRequest): MessageResponse

    @GET("products")
    suspend fun products(
        @Query("q") query: String? = null,
        @Query("category") category: String? = null,
        @Query("metal") metal: String? = null,
        @Query("min_price") minPrice: Double? = null,
        @Query("max_price") maxPrice: Double? = null,
        @Query("in_stock_only") inStockOnly: Boolean? = null,
        @Query("limit") limit: Int = 20
    ): List<ProductOut>

    @GET("products/{id}")
    suspend fun product(@Path("id") id: Int): ProductOut

    @GET("products/{id}/similar")
    suspend fun similarProducts(@Path("id") id: Int): List<ProductOut>

    @GET("featured-products")
    suspend fun featuredProducts(): List<ProductOut>

    @GET("seasonal-collections")
    suspend fun seasonalCollections(): List<SeasonalCollectionOut>

    @GET("categories")
    suspend fun categories(): List<CategoryOut>

    @POST("chat")
    suspend fun chat(@Body body: ChatRequest): ChatResponse

    @GET("chat/sessions")
    suspend fun chatSessions(): List<ChatSessionOut>

    @GET("chat/sessions/{sessionId}")
    suspend fun chatSession(@Path("sessionId") sessionId: String): ChatSessionDetailOut

    @POST("feedback")
    suspend fun feedback(@Body body: FeedbackCreate): MessageResponse

    @GET("wishlist")
    suspend fun wishlist(): List<SavedProductOut>

    @POST("wishlist")
    suspend fun addWishlist(@Body body: SavedItemCreate): SavedProductOut

    @DELETE("wishlist/{itemId}")
    suspend fun removeWishlist(@Path("itemId") itemId: Int): MessageResponse

    @GET("save-for-later")
    suspend fun saveForLater(): List<SavedProductOut>

    @POST("save-for-later")
    suspend fun addSaveForLater(@Body body: SavedItemCreate): SavedProductOut

    @DELETE("save-for-later/{itemId}")
    suspend fun removeSaveForLater(@Path("itemId") itemId: Int): MessageResponse

    @POST("request-callback")
    suspend fun requestCallback(@Body body: CallbackRequestCreate): CallbackRequestOut

    @GET("request-callbacks/my")
    suspend fun myCallbackRequests(): List<CallbackRequestOut>

    @POST("appointments")
    suspend fun bookAppointment(@Body body: AppointmentCreate): AppointmentOut

    @GET("appointments/my")
    suspend fun myAppointments(): List<AppointmentOut>

    @POST("custom-orders")
    suspend fun customOrder(@Body body: CustomOrderCreate): CustomOrderOut

    @GET("custom-orders/my")
    suspend fun myCustomOrders(): List<CustomOrderOut>

    @POST("complaints")
    suspend fun complaint(@Body body: ComplaintCreate): ComplaintOut

    @GET("complaints/my")
    suspend fun myComplaints(): List<ComplaintOut>

    @GET("orders/{orderReference}")
    suspend fun order(@Path("orderReference") orderReference: String): OrderLookupOut

    @POST("orders/support")
    suspend fun orderSupport(@Body body: OrderSupportCreate): OrderSupportOut

    @GET("orders/support/my")
    suspend fun myOrderSupport(): List<OrderSupportOut>

    @POST("orders/{orderReference}/cancel")
    suspend fun cancelOrder(
        @Path("orderReference") orderReference: String,
        @Body body: OrderActionRequest
    ): OrderActionOut

    @POST("orders/{orderReference}/return")
    suspend fun returnOrder(
        @Path("orderReference") orderReference: String,
        @Body body: OrderActionRequest
    ): OrderActionOut

    @POST("orders/{orderReference}/refund")
    suspend fun refundOrder(
        @Path("orderReference") orderReference: String,
        @Body body: OrderActionRequest
    ): OrderActionOut

    @GET("users/me/addresses")
    suspend fun addresses(): List<UserAddressOut>

    @POST("users/me/addresses")
    suspend fun createAddress(@Body body: UserAddressCreate): UserAddressOut

    @PATCH("users/me/addresses/{addressId}")
    suspend fun updateAddress(
        @Path("addressId") addressId: Int,
        @Body body: UserAddressUpdate
    ): UserAddressOut

    @DELETE("users/me/addresses/{addressId}")
    suspend fun deleteAddress(@Path("addressId") addressId: Int): MessageResponse

    @GET("users/me/notification-settings")
    suspend fun notificationSettings(): NotificationSettingsOut

    @PATCH("users/me/notification-settings")
    suspend fun updateNotificationSettings(
        @Body body: NotificationSettingsUpdate
    ): NotificationSettingsOut
}
```

## Data Models

Use Moshi `@Json(name = "...")` for snake_case fields:

```kotlin
data class TokenResponse(
    @Json(name = "access_token") val accessToken: String,
    @Json(name = "refresh_token") val refreshToken: String,
    @Json(name = "token_type") val tokenType: String = "bearer"
)

data class RefreshTokenRequest(
    @Json(name = "refresh_token") val refreshToken: String
)

data class RegisterRequest(
    val username: String,
    val email: String,
    val password: String
)

data class UserOut(
    val id: Int,
    val username: String,
    val email: String,
    @Json(name = "is_admin") val isAdmin: Boolean = false
)

data class ProductOut(
    val id: Int,
    val name: String,
    val description: String?,
    val sku: String?,
    val category: String?,
    val metal: String?,
    val price: Double,
    val image: String?,
    @Json(name = "in_stock") val inStock: Boolean,
    @Json(name = "stock_quantity") val stockQuantity: Int = 0,
    @Json(name = "is_featured") val isFeatured: Boolean = false
)

data class ChatRequest(
    val message: String,
    @Json(name = "session_id") val sessionId: String? = null
)

data class ChatResponse(
    @Json(name = "response_id") val responseId: String,
    val reply: String,
    val products: List<ProductOut> = emptyList(),
    @Json(name = "session_id") val sessionId: String?,
    val suggestions: List<String> = emptyList(),
    @Json(name = "applied_filters") val appliedFilters: Map<String, Any?> = emptyMap(),
    @Json(name = "result_count") val resultCount: Int = 0,
    @Json(name = "suggested_next_questions") val suggestedNextQuestions: List<String> = emptyList(),
    val intent: String?,
    val confidence: Double = 0.0,
    @Json(name = "answer_source") val answerSource: String,
    @Json(name = "tool_calls") val toolCalls: List<String> = emptyList(),
    val guardrails: List<String> = emptyList(),
    @Json(name = "lead_captured") val leadCaptured: Boolean = false,
    val handoff: HandoffInfo? = null
)

data class HandoffInfo(
    val reason: String,
    val message: String,
    val channels: List<String> = emptyList(),
    @Json(name = "lead_id") val leadId: Int?
)

data class SavedItemCreate(
    @Json(name = "product_id") val productId: Int,
    val note: String? = null
)

data class SavedProductOut(
    val id: Int,
    val product: ProductOut,
    val note: String?,
    @Json(name = "created_at") val createdAt: String
)

data class OrderActionRequest(
    val reason: String? = null,
    val message: String? = null
)

data class OrderActionOut(
    @Json(name = "order_reference") val orderReference: String,
    val action: String,
    @Json(name = "integration_status") val integrationStatus: String,
    val data: Map<String, Any?> = emptyMap(),
    val message: String? = null
)

data class OrderLookupOut(
    @Json(name = "order_reference") val orderReference: String,
    @Json(name = "integration_status") val integrationStatus: String,
    val data: Map<String, Any?> = emptyMap(),
    val message: String? = null
)

data class MessageResponse(val message: String)
```

Add the remaining models from OpenAPI when wiring each screen. The live schema is:

```text
https://sureshhari-snchatbot-backend.hf.space/openapi.json
```

## Token Storage

Use encrypted storage:

```kotlin
class TokenStore(private val encryptedPrefs: SharedPreferences) {
    fun accessToken(): String? = encryptedPrefs.getString("access_token", null)
    fun refreshToken(): String? = encryptedPrefs.getString("refresh_token", null)

    fun save(tokens: TokenResponse) {
        encryptedPrefs.edit()
            .putString("access_token", tokens.accessToken)
            .putString("refresh_token", tokens.refreshToken)
            .apply()
    }

    fun clear() {
        encryptedPrefs.edit().clear().apply()
    }
}
```

## Authorization Interceptor

Attach the access token to authenticated requests:

```kotlin
class AuthInterceptor(private val tokenStore: TokenStore) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = tokenStore.accessToken()
        val request = if (token.isNullOrBlank()) {
            chain.request()
        } else {
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        }
        return chain.proceed(request)
    }
}
```

## Refresh Token Authenticator

Use an OkHttp `Authenticator` to refresh once after a `401`. Keep refresh calls synchronized to prevent parallel refresh storms.

```kotlin
class TokenAuthenticator(
    private val tokenStore: TokenStore,
    private val refreshApi: JewelleryApi
) : Authenticator {
    private val lock = Any()

    override fun authenticate(route: Route?, response: Response): Request? {
        if (responseCount(response) >= 2) return null
        val refreshToken = tokenStore.refreshToken() ?: return null

        val newTokens = synchronized(lock) {
            runBlocking {
                try {
                    refreshApi.refresh(RefreshTokenRequest(refreshToken))
                } catch (_: Exception) {
                    null
                }
            }
        } ?: run {
            tokenStore.clear()
            return null
        }

        tokenStore.save(newTokens)
        return response.request.newBuilder()
            .header("Authorization", "Bearer ${newTokens.accessToken}")
            .build()
    }

    private fun responseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) {
            count++
            prior = prior.priorResponse
        }
        return count
    }
}
```

## Retrofit Setup

Use one unauthenticated client for login/refresh and one authenticated client for protected routes.

```kotlin
object ApiFactory {
    private const val BASE_URL = "https://sureshhari-snchatbot-backend.hf.space/"

    fun createPublicApi(moshi: Moshi): JewelleryApi {
        val client = OkHttpClient.Builder().build()
        return retrofit(client, moshi).create(JewelleryApi::class.java)
    }

    fun createAuthedApi(tokenStore: TokenStore, publicApi: JewelleryApi, moshi: Moshi): JewelleryApi {
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenStore))
            .authenticator(TokenAuthenticator(tokenStore, publicApi))
            .build()
        return retrofit(client, moshi).create(JewelleryApi::class.java)
    }

    private fun retrofit(client: OkHttpClient, moshi: Moshi): Retrofit {
        return Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(client)
            .addConverterFactory(MoshiConverterFactory.create(moshi))
            .build()
    }
}
```

## Screen Wiring

### Splash/Auth

On app start:

1. Call `GET /mobile/config`.
2. If access token exists, call `GET /me`.
3. If `GET /me` returns `401`, let the authenticator refresh.
4. If refresh fails, clear tokens and show login.

Login flow:

1. Call `POST /login`.
2. Save `access_token` and `refresh_token`.
3. Navigate to Home.

Signup flow:

1. Call `POST /register`.
2. Show verify-email reminder.
3. Offer `POST /resend-verification`.

Forgot password:

1. Call `POST /forgot-password`.
2. Show a generic success message.

### Home

Load in parallel:

- `GET /featured-products`
- `GET /seasonal-collections`
- `GET /categories`

Quick chat opens Chat screen with:

```kotlin
ChatRequest(message = "Show me gold rings under 20000")
```

### Chat

Use one stable `session_id` per conversation. Store it locally after first response.

Message send:

1. Add user bubble locally.
2. Show typing state.
3. Call `POST /chat`.
4. Add bot bubble with `reply`.
5. Render `products` as cards.
6. Render `suggested_next_questions` as quick replies.
7. If `handoff != null`, show callback/appointment CTA.

Feedback:

```kotlin
api.feedback(
    FeedbackCreate(
        responseId = chatResponse.responseId,
        feedbackType = "thumbs_up"
    )
)
```

### Product Detail

Load:

- `GET /products/{id}`
- `GET /products/{id}/similar`

Actions:

- Wishlist: `POST /wishlist`
- Save later: `POST /save-for-later`
- Ask bot: `POST /chat` with `message = "Tell me about ${product.name}"`

### Wishlist

Load:

- `GET /wishlist`

Actions:

- Remove: `DELETE /wishlist/{item_id}`
- Similar: `GET /products/{product_id}/similar`
- Ask bot: `POST /chat`

### Support

Callback:

- `POST /request-callback`

Appointment:

- `POST /appointments`

Complaint:

- `POST /complaints`

Custom order:

- `POST /custom-orders`

Order support:

- `POST /orders/support`

### Profile

Load:

- `GET /me`
- `GET /users/me/addresses`
- `GET /users/me/notification-settings`
- `GET /chat/sessions`
- `GET /wishlist`
- `GET /save-for-later`

Actions:

- Address create/update/delete via `/users/me/addresses`
- Notification update via `/users/me/notification-settings`
- Logout current device: `POST /logout` with refresh token
- Logout all devices: `POST /logout-all-devices`

### Orders

Lookup:

- `GET /orders/{order_reference}`

Actions:

- `POST /orders/{order_reference}/cancel`
- `POST /orders/{order_reference}/return`
- `POST /orders/{order_reference}/refund`
- `POST /orders/support`

Show `integration_status` clearly:

- `synced`: live OMS request succeeded.
- `capture_only`: request saved, OMS not configured.
- `failed`: request saved, OMS call failed.

## Error Handling

Backend validation errors are normalized:

```json
{
  "detail": "Validation error",
  "request_id": "...",
  "errors": [
    {
      "field": "body.message",
      "message": "Field required",
      "type": "missing"
    }
  ]
}
```

Android should show:

- field-level errors for `422`
- login error for `401`
- admin/customer forbidden message for `403`
- retry CTA for network timeout
- support fallback for repeated chat/order failures

## Final Integration Checklist

- `GET /mobile/config` called on startup.
- Token store uses encrypted storage.
- Refresh authenticator is wired.
- Login, signup, forgot password screens call backend.
- Home loads featured products, collections, categories.
- Chat sends messages, renders product cards and quick replies.
- Wishlist add/remove works.
- Product detail can ask bot about the item.
- Support forms submit successfully.
- Profile address and notification settings work.
- Chat history list/detail works.
- Orders screen handles `capture_only` until OMS is enabled.
- Logout clears local tokens.
