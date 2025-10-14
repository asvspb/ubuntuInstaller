/**
 * Sample JavaScript Template
 * 
 * This is a sample JavaScript template for the Ubuntu Installer Framework.
 */

// Strict mode to catch common coding mistakes
'use strict';

/**
 * Main application entry point
 */
function main() {
    console.log('Ubuntu Installer Framework JavaScript Template');
    
    // Example of DOM manipulation
    document.addEventListener('DOMContentLoaded', function() {
        console.log('DOM fully loaded and parsed');
        
        // Example of selecting an element
        const container = document.querySelector('.container');
        if (container) {
            container.style.border = '2px solid #007acc';
        }
        
        // Example of event listener
        const button = document.querySelector('#install-button');
        if (button) {
            button.addEventListener('click', function() {
                console.log('Install button clicked');
                performInstallation();
            });
        }
    });
}

/**
 * Perform installation process
 */
function performInstallation() {
    console.log('Starting installation process...');
    
    // Simulate installation steps
    const steps = [
        'Checking system requirements',
        'Downloading packages',
        'Installing components',
        'Configuring system',
        'Finalizing installation'
    ];
    
    steps.forEach((step, index) => {
        setTimeout(() => {
            console.log(`Step ${index + 1}: ${step}`);
        }, index * 1000);
    });
    
    // Completion message
    setTimeout(() => {
        console.log('Installation completed successfully!');
    }, steps.length * 1000);
}

/**
 * Utility function to display messages
 * @param {string} message - Message to display
 * @param {string} type - Type of message (info, success, warning, error)
 */
function showMessage(message, type = 'info') {
    const messageContainer = document.getElementById('message-container');
    if (!messageContainer) {
        console.warn('Message container not found');
        return;
    }
    
    // Create message element
    const messageElement = document.createElement('div');
    messageElement.className = `message ${type}`;
    messageElement.textContent = message;
    
    // Add to container
    messageContainer.appendChild(messageElement);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (messageElement.parentNode === messageContainer) {
            messageContainer.removeChild(messageElement);
        }
    }, 5000);
}

// Run main function when script loads
main();

// Export functions for testing (if needed)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        main,
        performInstallation,
        showMessage
    };
}