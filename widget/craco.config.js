// CRA build override: alias React imports to preact/compat so the widget
// bundle ships as ~35 KB gzip instead of ~180 KB. The application source
// keeps "import React from 'react'" syntax — webpack rewrites it at build
// time. The admin dashboard build is unaffected (different package).
//
// Why preact/compat (not preact directly): every component in this widget
// imports from 'react'/'react-dom' and uses ReactMarkdown which expects a
// React.createElement-compatible runtime. preact/compat provides that
// shim while keeping Preact's small renderer.
module.exports = {
  webpack: {
    alias: {
      react: 'preact/compat',
      'react-dom/test-utils': 'preact/test-utils',
      'react-dom': 'preact/compat',
      'react/jsx-runtime': 'preact/jsx-runtime',
    },
  },
  jest: {
    configure: {
      moduleNameMapper: {
        '^react$': 'preact/compat',
        '^react-dom$': 'preact/compat',
        '^react-dom/test-utils$': 'preact/test-utils',
        '^react/jsx-runtime$': 'preact/jsx-runtime',
      },
    },
  },
};
