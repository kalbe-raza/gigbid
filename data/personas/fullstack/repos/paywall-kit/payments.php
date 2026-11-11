<?php
\Stripe\Stripe::setApiKey(getenv('STRIPE_KEY'));
$intent = \Stripe\PaymentIntent::create(['amount' => $total, 'currency' => 'usd']);
