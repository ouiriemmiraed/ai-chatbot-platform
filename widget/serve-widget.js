const express = require('express');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = 3001;

// Enable CORS for all origins
app.use(cors());

// Serve the widget bundle
app.use(express.static(path.join(__dirname, 'build')));

app.listen(PORT, () => {
    console.log(`✅ Widget server running on http://localhost:${PORT}`);
    console.log(`📦 Serving widget.bundle.js from build directory`);
});
