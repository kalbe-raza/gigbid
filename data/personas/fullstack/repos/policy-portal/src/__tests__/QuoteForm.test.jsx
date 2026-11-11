import { render, screen } from '@testing-library/react';

test('shows premium after submit', async () => {
  render(<QuoteForm />);
  expect(await screen.findByText(/premium/i)).toBeInTheDocument();
});
