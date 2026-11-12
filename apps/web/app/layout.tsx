import './globals.css';
export const metadata = {
  title: 'Bithumb · Manual Signal Assistant',
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: {
      index: false,
      follow: false,
      noimageindex: true,
      'max-video-preview': -1,
      'max-image-preview': 'none',
      'max-snippet': -1,
    },
  },
  icons: {
    icon: [
      { url: '/icon.svg', type: 'image/svg+xml' },
      { url: '/favicon.ico', sizes: '32x32' },
    ],
    apple: '/icon.svg',
  },
};
export default function Layout({children}:{children:React.ReactNode}) { return <html lang="ko"><body>{children}</body></html>; }
