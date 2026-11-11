const transfer = await stripe.transfers.create({ amount, currency: 'usd', destination: freelancer.stripeAccountId });
